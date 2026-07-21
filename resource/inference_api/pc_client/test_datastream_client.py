import asyncio
import base64
import csv
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


MODULE_PATH = Path(__file__).with_name("datastream_client.py")
SPEC = importlib.util.spec_from_file_location("datastream_client", MODULE_PATH)
datastream_client = importlib.util.module_from_spec(SPEC)
sys.modules["datastream_client"] = datastream_client
assert SPEC.loader is not None
SPEC.loader.exec_module(datastream_client)


class FakeSource(datastream_client.InferenceSource):
    source_type = datastream_client.DEFAULT_SOURCE_TYPE
    display_name = "Fake Beetle"
    instances = []

    def __init__(self, config):
        super().__init__(config)
        self.connected = False
        self.menu_requests = 0
        self.sent_choices = []
        FakeSource.instances.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def read_line(self):
        await asyncio.sleep(0.05)
        return ""

    async def send_gesture_selection(self, choice):
        self.sent_choices.append(choice)

    async def request_menu(self):
        self.menu_requests += 1


class FakeCaptureSource(datastream_client.InferenceSource):
    source_type = "fake_capture"
    display_name = "Fake capture source"
    uses_line_reader = False

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def request_menu(self):
        return None

    async def capture_window(self, *, window_id, selection, repetition, target):
        packet = datastream_client.pack_result_packet(
            deployment=2,
            window_id=window_id,
            source_sequence=33,
            inference_us=700,
            confidence=0.9,
            repetition=repetition,
            predicted_class=1,
            ok=True,
            trusted=True,
            correct=True,
        )
        return packet, {
            "type": "inference_result",
            "deployment_id": 2,
            "window_id": window_id,
            "repetition": repetition,
            "target": target,
            "predicted": "Flexion",
            "correct": True,
            "trusted": True,
            "confidence": 0.9,
            "collect_ms": 1940.0,
            "inference_ms": 0.7,
            "scores": {"Flexion": 0.9},
        }


class FailingEdgeSource(datastream_client.InferenceSource):
    source_type = "failing_edge"

    async def connect(self):
        # Yield to make concurrent Connect requests overlap without a lock.
        await asyncio.sleep(0)
        raise datastream_client.ImuSourceError("incompatible BLE firmware")


class FakeRawSource(datastream_client.InferenceSource):
    source_type = "fake_raw"
    display_name = "Fake raw IMU"
    uses_line_reader = False

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def request_menu(self):
        return None

    async def capture_samples(self):
        return SimpleNamespace(
            features=lambda: [0.0] * 198,
            source_sequence=55,
            capture_ms=1940.0,
            device_span_ms=1939.392,
            mean_interval_ms=60.606,
        )


class ProtocolTest(unittest.TestCase):
    def test_resolves_gesture_by_number_and_case_insensitive_label(self):
        menu = beetle_menu()

        by_number = datastream_client.resolve_gesture_selection(menu, "2")
        by_label = datastream_client.resolve_gesture_selection(menu, "flexion")
        by_spaced_label = datastream_client.resolve_gesture_selection(
            menu,
            "radial deviation",
        )

        self.assertEqual(by_number.label, "Extension")
        self.assertEqual(by_label.selection, 1)
        self.assertEqual(by_spaced_label.selection, 5)
        self.assertIsNone(datastream_client.resolve_gesture_selection(menu, "999"))

    def test_rest_envelope_excludes_raw_sensor_payloads(self):
        received_at = "2026-06-25T12:00:00+00:00"
        event = {
            "type": "inference_result",
            "device_id": "beetle_rp2530_001",
            "repetition": 1,
            "predicted": "Flexion",
            "confidence": 0.91,
            "scores": {"Flexion": 0.91},
            "features": [1, 2, 3],
            "raw": "serial line",
        }

        envelope = datastream_client.to_rest_envelope(event, received_at)

        self.assertEqual(envelope["type"], "inference_result")
        self.assertEqual(envelope["received_at"], received_at)
        self.assertEqual(envelope["data"]["predicted"], "Flexion")
        self.assertNotIn("features", envelope["data"])
        self.assertNotIn("raw", envelope["data"])

    def test_jsonl_logger_uses_python_client_record_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = datastream_client.JsonlLogger(
                Path(temp_dir),
                filename_factory=lambda: "session.jsonl",
            )
            logger.append_valid(
                "2026-06-25T12:00:00+00:00",
                {"type": "inference_result", "predicted": "Flexion"},
            )
            logger.close()

            record = json.loads((Path(temp_dir) / "session.jsonl").read_text())

        self.assertEqual(record["received_at"], "2026-06-25T12:00:00+00:00")
        self.assertEqual(record["data"]["type"], "inference_result")
        self.assertEqual(record["data"]["predicted"], "Flexion")


class ApiHubTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeSource.instances.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.hub = datastream_client.ApiHub(
            {datastream_client.DEFAULT_SOURCE_TYPE: FakeSource}
        )
        self.hub.configure(
            {
                "source_type": datastream_client.DEFAULT_SOURCE_TYPE,
                "port": "COM13",
                "baud": 115200,
                "log_dir": self.temp_dir.name,
            }
        )

    async def asyncTearDown(self):
        await self.hub.disconnect()

    async def test_state_transitions_and_gesture_send(self):
        await self.hub.connect()
        self.assertEqual(FakeSource.instances[-1].menu_requests, 1)
        await self.hub.request_menu()
        self.assertEqual(FakeSource.instances[-1].menu_requests, 2)
        await self.hub.handle_serial_line(json_line(beetle_menu()))

        result = await self.hub.start_session({"gesture": "Flexion"})

        self.assertEqual(result["selected"]["selection"], 1)
        self.assertTrue(self.hub.session_running)
        self.assertEqual(FakeSource.instances[-1].sent_choices[0].selection, 1)

    async def test_rest_event_feed_receives_safe_result_and_metrics(self):
        await self.hub.handle_serial_line(
            json_line(
                {
                    "type": "inference_result",
                    "device_id": "beetle_rp2530_001",
                    "repetition": 1,
                    "predicted": "Flexion",
                    "confidence": 0.91,
                    "scores": {"Flexion": 0.91},
                    "features": [1, 2, 3],
                }
            )
        )

        events = self.hub.events_after()["events"]
        result_envelope = next(
            event for event in events if event["type"] == "inference_result"
        )
        metrics_envelope = next(event for event in events if event["type"] == "metrics")

        self.assertNotIn("features", result_envelope["data"])
        self.assertEqual(result_envelope["data"]["predicted"], "Flexion")
        self.assertEqual(metrics_envelope["data"]["valid_count"], 1)
        self.assertEqual(metrics_envelope["data"]["invalid_count"], 0)

    async def test_invalid_lines_increment_metrics_without_streaming_raw_line(self):
        await self.hub.handle_serial_line("not json")
        metrics_envelope = next(
            event
            for event in self.hub.events_after()["events"]
            if event["type"] == "metrics"
        )

        self.assertEqual(metrics_envelope["data"]["valid_count"], 0)
        self.assertEqual(metrics_envelope["data"]["invalid_count"], 1)
        self.assertNotIn("raw", json.dumps(metrics_envelope))

    async def test_concurrent_failed_connects_are_serialized_and_cleaned_up(self):
        hub = datastream_client.ApiHub({FailingEdgeSource.source_type: FailingEdgeSource})
        hub.configure(
            {
                "source_type": FailingEdgeSource.source_type,
                "log_dir": self.temp_dir.name,
            }
        )

        results = await asyncio.gather(
            hub.connect(),
            hub.connect(),
            return_exceptions=True,
        )

        self.assertTrue(
            all(isinstance(result, datastream_client.ClientError) for result in results)
        )
        self.assertEqual(
            [str(result) for result in results],
            ["incompatible BLE firmware", "incompatible BLE firmware"],
        )
        self.assertIsNone(hub._source)
        self.assertIsNone(hub._logging)
        self.assertEqual(hub.last_error, "incompatible BLE firmware")

    async def test_raw_imu_and_model_backend_are_coordinated_separately(self):
        backend = SimpleNamespace(
            deployment_id=0,
            start=AsyncMock(),
            close=AsyncMock(),
            classify=AsyncMock(
                return_value=SimpleNamespace(
                    scores=(0.05, 0.8, 0.05, 0.04, 0.03, 0.03),
                    inference_us=600,
                    backend="local",
                    model_version="19",
                )
            ),
        )
        hub = datastream_client.ApiHub({FakeRawSource.source_type: FakeRawSource})
        hub.configure(
            {
                "source_type": FakeRawSource.source_type,
                "model_backend": "local",
                "log_dir": self.temp_dir.name,
            }
        )
        with patch.object(
            datastream_client,
            "create_model_backend",
            return_value=backend,
        ):
            await hub.connect()
            detail = await hub.capture(
                {"window_id": 77, "selection": 1, "repetition": 1}
            )
            await hub.disconnect()

        backend.start.assert_awaited_once()
        backend.classify.assert_awaited_once()
        backend.close.assert_awaited_once()
        self.assertEqual(detail["deployment"], "local")
        self.assertEqual(detail["predicted"], "Flexion")
        self.assertEqual(detail["source_sequence"], 55)
        self.assertGreaterEqual(detail["post_capture_ms"], 0)
        self.assertGreaterEqual(detail["pc_pipeline_ms"], detail["post_capture_ms"])
        self.assertGreater(detail["pc_rss_bytes"], 0)
        self.assertEqual(
            detail["experiment_profile"]["feedback_deadline_ms"],
            500.0,
        )


@unittest.skipUnless(
    hasattr(datastream_client, "create_app"),
    "FastAPI app factory unavailable",
)
class RestApiTest(unittest.TestCase):
    def test_rest_health_config_and_state(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            hub = datastream_client.ApiHub(
                {datastream_client.DEFAULT_SOURCE_TYPE: FakeSource}
            )
            app = datastream_client.create_app(hub)
            client = TestClient(app)

            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["ok"])
            self.assertEqual(health.json()["transport"], "rest")
            self.assertEqual(health.json()["model_backends"], ["local", "edge", "cloud"])
            self.assertEqual(health.json()["model_contract"]["project_id"], 738400)
            self.assertEqual(health.json()["model_contract"]["sample_count"], 33)
            self.assertEqual(health.json()["model_contract"]["units"][0], "g")

            config = client.put(
                "/api/config",
                json={
                    "source_type": datastream_client.DEFAULT_SOURCE_TYPE,
                    "port": "COM13",
                    "baud": 115200,
                    "log_dir": temp_dir,
                },
            )
            self.assertEqual(config.status_code, 200)
            self.assertEqual(config.json()["config"]["port"], "COM13")
            self.assertEqual(config.json()["config"]["model_backend"], "local")
            self.assertNotIn("model_api_key", config.json()["config"])

            state = client.get("/api/state")
            self.assertEqual(state.status_code, 200)
            self.assertFalse(state.json()["connected"])

    def test_rest_event_feed_exposes_state_changes(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            hub = datastream_client.ApiHub(
                {datastream_client.DEFAULT_SOURCE_TYPE: FakeSource}
            )
            hub.configure(
                {
                    "source_type": datastream_client.DEFAULT_SOURCE_TYPE,
                    "log_dir": temp_dir,
                }
            )
            app = datastream_client.create_app(hub)
            client = TestClient(app)

            self.assertEqual(client.post("/api/source/connect").status_code, 200)
            feed = client.get("/api/events?after_id=0").json()
            self.assertTrue(any(event["type"] == "state" for event in feed["events"]))
            self.assertGreater(feed["latest_event_id"], 0)
            self.assertEqual(client.post("/api/source/disconnect").status_code, 200)

    def test_http_capture_returns_durable_packet_and_detail(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            hub = datastream_client.ApiHub({"fake_capture": FakeCaptureSource})
            hub.configure({"source_type": "fake_capture", "log_dir": temp_dir})
            client = TestClient(datastream_client.create_app(hub))
            self.assertEqual(client.post("/api/source/connect").status_code, 200)

            response = client.post(
                "/api/capture",
                json={
                    "type": "capture",
                    "window_id": 43,
                    "selection": 1,
                    "repetition": 1,
                },
            )

            self.assertEqual(response.status_code, 200)
            body = response.json()
            packet = base64.b64decode(body["packet_base64"])
            self.assertEqual(
                datastream_client.unpack_result_packet(packet).window_id,
                43,
            )
            self.assertEqual(body["detail"]["collect_ms"], 1940.0)
            cached = client.get("/api/captures/43")
            self.assertEqual(cached.status_code, 200)
            self.assertEqual(cached.json(), body)
            self.assertEqual(client.get("/api/captures/999").status_code, 404)
            self.assertEqual(client.post("/api/source/disconnect").status_code, 200)

    def test_queued_http_capture_is_idempotent_and_pollable(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            hub = datastream_client.ApiHub({"fake_capture": FakeCaptureSource})
            hub.configure({"source_type": "fake_capture", "log_dir": temp_dir})
            with TestClient(datastream_client.create_app(hub)) as client:
                self.assertEqual(client.post("/api/source/connect").status_code, 200)
                payload = {
                    "type": "capture",
                    "window_id": 44,
                    "selection": 1,
                    "repetition": 1,
                }
                first = client.post("/api/captures", json=payload)
                second = client.post("/api/captures", json=payload)
                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 202)

                result = None
                for _ in range(20):
                    response = client.get("/api/captures/44")
                    if response.status_code == 200:
                        result = response.json()
                        break
                self.assertIsNotNone(result)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["detail"]["window_id"], 44)
                self.assertEqual(hub.stats.valid_count, 1)
                self.assertEqual(client.post("/api/source/disconnect").status_code, 200)

    def test_mobile_session_page_uses_http_polling_without_websocket(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        app = datastream_client.create_app()
        client = TestClient(app)
        response = client.get("/mobile")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/captures", response.text)
        self.assertIn("Start 10 repetitions", response.text)
        self.assertNotIn("new WebSocket", response.text)
        self.assertNotIn("/ws/results", {route.path for route in app.routes})
        dashboard = client.get("/")
        self.assertIn('id="modelBackend"', dashboard.text)
        self.assertIn("/api/events", dashboard.text)

    def test_dashboard_polling_preserves_unsaved_config_fields(self):
        dashboard = datastream_client.web_gui_html()

        self.assertIn("const dirtyConfigFields = new Set();", dashboard)
        self.assertIn("if (dirtyConfigFields.has(id)) return;", dashboard)
        self.assertIn("const submittedRevision = configEditRevision;", dashboard)
        self.assertIn("dirtyConfigFields.clear();", dashboard)
        self.assertIn("markConfigDirty('bleDeviceId');", dashboard)
        self.assertIn(
            "renderConfigValue('modelUrl', state.config?.model_url || '');",
            dashboard,
        )
        self.assertNotIn(
            "$('modelUrl').value = state.config?.model_url || '';",
            dashboard,
        )
        self.assertIn("dirtyExperimentFields", dashboard)
        self.assertIn("renderExperimentProfile", dashboard)
        self.assertIn('id="benchmarkRows"', dashboard)

        mobile = datastream_client.mobile_session_html()
        self.assertIn("profileSnapshot = Object.freeze", mobile)
        self.assertIn("pendingBenchmarkRecords", mobile)
        self.assertIn("saveFailedBenchmark", mobile)
        self.assertIn("post-capture", mobile)

    def test_experiment_profile_validation_and_state(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        client = TestClient(datastream_client.create_app())
        response = client.put(
            "/api/experiment-profile",
            json={
                "experiment_label": "rahti-wifi",
                "network_profile": "campus Wi-Fi",
                "platform": "Rahti",
                "region": "2.rahti",
                "cpu_limit_millicores": 1000,
                "memory_limit_mib": 512,
                "concurrency": 4,
                "run_type": "steady_state",
                "feedback_deadline_ms": 500,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["concurrency"], 4)
        self.assertEqual(
            client.get("/api/state").json()["experiment_profile"]["experiment_label"],
            "rahti-wifi",
        )
        invalid = client.put(
            "/api/experiment-profile",
            json={"run_type": "sometimes", "concurrency": 1},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_benchmark_deduplication_summary_and_csv_export(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            hub = datastream_client.ApiHub()
            hub.configure({"log_dir": temp_dir})
            client = TestClient(datastream_client.create_app(hub))
            record = {
                "type": "benchmark_record",
                "session_id": "session-a",
                "deployment_id": 1,
                "gesture": "Flexion",
                "window_id": 7,
                "repetition": 1,
                "correct": True,
                "confidence": 0.9,
                "capture_ms": 1940,
                "inference_ms": 2,
                "end_to_end_ms": 1950,
                "non_capture_ms": 8,
            }
            first = client.post("/api/benchmarks/records", json=record).json()
            second = client.post("/api/benchmarks/records", json=record).json()

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            summary = client.get("/api/benchmarks/summary").json()
            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["groups"][0]["accuracy"], 1.0)
            export = client.get("/api/benchmarks/export.csv")
            self.assertEqual(export.status_code, 200)
            self.assertIn("session-a", export.text)

    def test_benchmark_v2_success_failure_percentiles_and_nullable_export(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            hub = datastream_client.ApiHub()
            hub.configure({"log_dir": temp_dir})
            client = TestClient(datastream_client.create_app(hub))
            base = {
                "type": "benchmark_record",
                "session_id": "session-v2",
                "deployment": "cloud",
                "deployment_id": 2,
                "gesture": "Flexion",
                "model_version": "cloud-19",
                "repetition": 1,
                "experiment_label": "rahti-wifi",
                "network_profile": "campus Wi-Fi",
                "platform": "Rahti",
                "region": "2.rahti",
                "concurrency": 1,
                "run_type": "steady_state",
                "feedback_deadline_ms": 500,
            }
            success = {
                **base,
                "window_id": 70,
                "attempt": 1,
                "outcome": "success",
                "correct": True,
                "trusted": True,
                "confidence": 0.9,
                "post_capture_ms": 120,
                "backend_wall_ms": 40,
                "server_ms": 10,
                "transport_residual_ms": 30,
                "backend_peak_rss_bytes": 64000000,
            }
            failure = {
                **base,
                "window_id": 71,
                "attempt": 2,
                "outcome": "timeout",
                "error_code": "model_timeout",
            }
            self.assertEqual(client.post("/api/benchmarks/records", json=success).status_code, 200)
            self.assertEqual(client.post("/api/benchmarks/records", json=failure).status_code, 200)

            group = client.get("/api/benchmarks/summary").json()["groups"][0]
            self.assertEqual(group["attempt_count"], 2)
            self.assertEqual(group["success_count"], 1)
            self.assertEqual(group["timeout_count"], 1)
            self.assertEqual(group["deadline_misses"], 0)
            self.assertEqual(group["post_capture_ms"]["p99"], 120)
            self.assertEqual(group["peak_backend_rss_bytes"], 64000000)

            exported = client.get("/api/benchmarks/export.csv").text
            rows = list(csv.DictReader(io.StringIO(exported)))
            failed_row = next(row for row in rows if row["outcome"] == "timeout")
            self.assertEqual(failed_row["post_capture_ms"], "")
            self.assertEqual(failed_row["error_code"], "model_timeout")

    def test_legacy_benchmark_row_is_read_without_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = datastream_client.BenchmarkStore(Path(temp_dir))
            legacy = {
                "session_id": "legacy",
                "deployment": "local",
                "deployment_id": 0,
                "gesture": "Flexion",
                "model_version": "19",
                "window_id": 1,
                "repetition": 1,
                "correct": True,
                "confidence": 0.9,
                "capture_ms": 1940,
                "inference_ms": 2,
                "end_to_end_ms": 1950,
                "non_capture_ms": 8,
            }
            store.path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

            summary = store.summary()

            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["groups"][0]["success_count"], 1)
            self.assertEqual(summary["groups"][0]["accuracy"], 1.0)
            self.assertNotIn("schema_version", store.records()[0])


def beetle_menu():
    return {
        "type": "gesture_menu",
        "device_id": "beetle_rp2530_001",
        "prompt": "select_gesture_1_to_6",
        "gestures": [
            {"selection": 1, "label": "Flexion"},
            {"selection": 2, "label": "Extension"},
            {"selection": 3, "label": "Pronation"},
            {"selection": 4, "label": "Supination"},
            {"selection": 5, "label": "Radial Deviation"},
            {"selection": 6, "label": "Ulnar Deviation"},
        ],
    }


def json_line(data):
    return json.dumps(data)


if __name__ == "__main__":
    unittest.main()
