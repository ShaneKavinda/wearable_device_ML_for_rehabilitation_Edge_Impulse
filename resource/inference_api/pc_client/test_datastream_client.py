import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_mobile_envelope_excludes_raw_sensor_payloads(self):
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

        envelope = datastream_client.to_mobile_envelope(event, received_at)

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

    async def test_subscriber_receives_mobile_safe_result_and_metrics(self):
        queue = self.hub.subscribe()

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

        result_envelope = await next_envelope(queue, "inference_result")
        metrics_envelope = await next_envelope(queue, "metrics")

        self.assertNotIn("features", result_envelope["data"])
        self.assertEqual(result_envelope["data"]["predicted"], "Flexion")
        self.assertEqual(metrics_envelope["data"]["valid_count"], 1)
        self.assertEqual(metrics_envelope["data"]["invalid_count"], 0)

    async def test_invalid_lines_increment_metrics_without_streaming_raw_line(self):
        queue = self.hub.subscribe()

        await self.hub.handle_serial_line("not json")

        metrics_envelope = await next_envelope(queue, "metrics")

        self.assertEqual(metrics_envelope["data"]["valid_count"], 0)
        self.assertEqual(metrics_envelope["data"]["invalid_count"], 1)
        self.assertNotIn("raw", json.dumps(metrics_envelope))


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

            state = client.get("/api/state")
            self.assertEqual(state.status_code, 200)
            self.assertFalse(state.json()["connected"])

    def test_websocket_results_accepts_and_sends_initial_state(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI TestClient is not installed")

        hub = datastream_client.ApiHub(
            {datastream_client.DEFAULT_SOURCE_TYPE: FakeSource}
        )
        app = datastream_client.create_app(hub)
        client = TestClient(app)

        with client.websocket_connect("/ws/results") as websocket:
            envelope = websocket.receive_json()

        self.assertEqual(envelope["type"], "state")
        self.assertFalse(envelope["data"]["connected"])


async def next_envelope(queue, envelope_type):
    for _ in range(10):
        envelope = await asyncio.wait_for(queue.get(), timeout=0.5)
        if envelope["type"] == envelope_type:
            return envelope
    raise AssertionError(f"Did not receive envelope type {envelope_type}")


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
