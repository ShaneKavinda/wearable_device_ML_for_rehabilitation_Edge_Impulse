import asyncio
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from resource.inference_api.pc_client import imu_source
from resource.inference_api.pc_client import inference_protocol
from resource.inference_api.pc_client import model_backends


class ProtocolTest(unittest.TestCase):
    def test_raw_packet_layout_and_unit_conversion(self):
        data = struct.pack("<II6h", 7, 99, 16384, -16384, 8192, 131, -262, 0)
        packet = imu_source.RawImuPacket.decode(data)

        self.assertEqual(packet.sequence, 7)
        self.assertAlmostEqual(packet.features()[0], 1.0, places=5)
        self.assertAlmostEqual(packet.features()[1], -1.0, places=5)
        self.assertAlmostEqual(packet.features()[2], 0.5, places=5)
        self.assertEqual(packet.features()[3], 1.0)
        self.assertEqual(packet.features()[4], -2.0)

    def test_result_packet_is_twenty_bytes_and_q15(self):
        packed = inference_protocol.pack_result_packet(
            deployment=2,
            window_id=42,
            source_sequence=9,
            inference_us=1000,
            confidence=0.5,
            repetition=3,
            predicted_class=1,
            ok=True,
            trusted=True,
            correct=False,
        )
        result = inference_protocol.unpack_result_packet(packed)

        self.assertEqual(len(packed), 20)
        self.assertTrue(result.ok)
        self.assertTrue(result.trusted)
        self.assertFalse(result.correct)
        self.assertAlmostEqual(result.confidence, 0.5, places=4)


class CaptureTest(unittest.IsolatedAsyncioTestCase):
    def make_source(self):
        config = SimpleNamespace(ble_device_id=None, ble_name="IMU-Raw-Stream")
        source = imu_source.XiaoBleImuSource(config)
        source._client = SimpleNamespace(is_connected=True)
        return source

    async def test_collects_only_active_window_and_handles_timestamp_rollover(self):
        source = self.make_source()
        source._on_notification(
            None,
            bytearray(struct.pack("<II6h", 1, 1, *([0] * 6))),
        )
        task = asyncio.create_task(source.capture_samples())
        await asyncio.sleep(0)
        first_timestamp = 0xFFFFFFFF - 100000
        for index in range(33):
            timestamp = (first_timestamp + index * 60606) & 0xFFFFFFFF
            source._on_notification(
                None,
                bytearray(
                    struct.pack("<II6h", 100 + index, timestamp, *([0] * 6))
                ),
            )

        captured = await task
        self.assertEqual(captured.source_sequence, 132)
        self.assertEqual(len(captured.features()), 198)
        self.assertAlmostEqual(captured.device_span_ms, 32 * 60.606, places=2)
        self.assertAlmostEqual(captured.mean_interval_ms, 60.606, places=2)

    async def test_wrong_sampling_rate_invalidates_capture(self):
        source = self.make_source()
        task = asyncio.create_task(source.capture_samples())
        await asyncio.sleep(0)
        for index in range(inference_protocol.SAMPLE_COUNT):
            source._on_notification(
                None,
                bytearray(
                    struct.pack(
                        "<II6h",
                        index,
                        index * 50000,  # 20 Hz is not deployment 19's 16.5 Hz.
                        *([0] * 6),
                    )
                ),
            )

        with self.assertRaisesRegex(
            imu_source.SamplingContractError,
            "expected 16.5 Hz",
        ):
            await task

    def test_sampling_contract_matches_exported_model(self):
        self.assertEqual(inference_protocol.MODEL_PROJECT_ID, 738400)
        self.assertEqual(inference_protocol.MODEL_DEPLOY_VERSION, 19)
        self.assertEqual(inference_protocol.SAMPLE_COUNT, 33)
        self.assertEqual(inference_protocol.AXES_PER_SAMPLE, 6)
        self.assertEqual(inference_protocol.FEATURE_COUNT, 198)
        self.assertAlmostEqual(inference_protocol.MODEL_FREQUENCY_HZ, 16.5)
        self.assertEqual(
            inference_protocol.FEATURE_AXES,
            ("acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"),
        )
        self.assertEqual(
            inference_protocol.FEATURE_UNITS,
            ("g", "g", "g", "deg/s", "deg/s", "deg/s"),
        )

    async def test_sequence_gap_invalidates_capture(self):
        source = self.make_source()
        task = asyncio.create_task(source.capture_samples())
        await asyncio.sleep(0)
        source._on_notification(
            None,
            bytearray(struct.pack("<II6h", 10, 1, *([0] * 6))),
        )
        source._on_notification(
            None,
            bytearray(struct.pack("<II6h", 12, 2, *([0] * 6))),
        )

        with self.assertRaises(imu_source.SequenceGapError):
            await task


class RunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_runner_fails_without_fake_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = model_backends.EdgeInferenceRunner(Path(directory) / "missing.exe")
            with self.assertRaisesRegex(
                model_backends.ModelBackendError,
                "fake inference is not used",
            ):
                await runner.start()

    async def test_persistent_runner_binary_protocol(self):
        response = model_backends.RUNNER_RESPONSE.pack(
            model_backends.RUNNER_RESPONSE_MAGIC,
            123,
            0,
            456,
            0.1,
            0.2,
            0.3,
            0.15,
            0.15,
            0.1,
        )
        process = FakeProcess(response)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "runner.exe"
            executable.write_bytes(b"test")
            runner = model_backends.EdgeInferenceRunner(executable)
            with patch.object(
                model_backends.asyncio,
                "create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ):
                inference_us, scores = await runner.classify(123, [1.0] * 198)
                await runner.stop()

        magic, window_id, count = model_backends.RUNNER_REQUEST_HEADER.unpack(
            bytes(process.stdin.data[: model_backends.RUNNER_REQUEST_HEADER.size])
        )
        self.assertEqual((magic, window_id, count), (b"EIQ1", 123, 198))
        self.assertEqual(inference_us, 456)
        self.assertAlmostEqual(scores[2], 0.3, places=5)

    async def test_runner_restarts_once_after_unexpected_exit(self):
        failed = FakeProcess(
            asyncio.IncompleteReadError(
                partial=b"",
                expected=model_backends.RUNNER_RESPONSE.size,
            )
        )
        response = model_backends.RUNNER_RESPONSE.pack(
            model_backends.RUNNER_RESPONSE_MAGIC,
            9,
            0,
            10,
            *([1 / 6] * 6),
        )
        recovered = FakeProcess(response)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "runner.exe"
            executable.write_bytes(b"test")
            runner = model_backends.EdgeInferenceRunner(executable)
            factory = AsyncMock(side_effect=[failed, recovered])
            with patch.object(
                model_backends.asyncio,
                "create_subprocess_exec",
                new=factory,
            ):
                inference_us, _ = await runner.classify(9, [0.0] * 198)
                await runner.stop()

        self.assertEqual(factory.await_count, 2)
        self.assertEqual(inference_us, 10)


class ModelBackendTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_backend_reports_local_deployment(self):
        backend = model_backends.LocalModelBackend(Path("runner.exe"), "19")
        backend.runner.classify = AsyncMock(
            return_value=(25, (0.1, 0.6, 0.1, 0.1, 0.05, 0.05))
        )
        result = await backend.classify(7, [0.0] * 198)

        self.assertEqual(result.backend, "local")
        self.assertEqual(backend.deployment_id, 0)
        self.assertEqual(result.inference_us, 25)
        self.assertIsNone(result.transport_residual_us)
        self.assertGreaterEqual(result.backend_overhead_us, 0)

    async def test_rest_backend_accepts_label_score_mapping(self):
        backend = model_backends.RestModelBackend(
            backend="cloud",
            url="https://model.example/infer",
            api_key="secret",
            timeout_s=2,
            model_version="19",
        )
        backend._post_json = lambda payload: {
            "scores": {
                label: (0.75 if label == "Flexion" else 0.05)
                for label in inference_protocol.MODEL_LABELS
            },
            "inference_us": 1200,
            "model_version": "cloud-3",
        }

        result = await backend.classify(8, [0.0] * 198)

        self.assertEqual(result.backend, "cloud")
        self.assertEqual(backend.deployment_id, 2)
        self.assertEqual(result.model_version, "cloud-3")
        self.assertEqual(result.scores[1], 0.75)
        self.assertIsNone(result.server_us)
        self.assertIsNone(result.transport_residual_us)

    async def test_rest_backend_keeps_http_and_server_timing_separate(self):
        backend = model_backends.RestModelBackend(
            backend="cloud",
            url="https://model.example/infer",
            api_key="secret",
            timeout_s=2,
            model_version="19",
        )
        response_body = {
            "scores": [0.1, 0.5, 0.1, 0.1, 0.1, 0.1],
            "inference_us": 1200,
            "timing_us": {"queue": 25, "server": 1300},
            "resource_usage": {
                "process_tree_rss_bytes": 60000000,
                "process_tree_peak_rss_bytes": 61000000,
                "request_cpu_us": 1400,
            },
        }
        backend._post_json = lambda payload: model_backends._HttpJsonResponse(
            body=response_body,
            request_bytes=2048,
            response_bytes=512,
        )

        result = await backend.classify(8, [0.0] * 198)

        self.assertEqual(result.inference_us, 1200)
        self.assertEqual(result.server_us, 1300)
        self.assertEqual(result.queue_us, 25)
        self.assertEqual(
            result.transport_residual_us,
            max(0, result.backend_wall_us - result.server_us),
        )
        self.assertEqual(result.request_bytes, 2048)
        self.assertEqual(result.response_bytes, 512)
        self.assertEqual(result.backend_rss_bytes, 60000000)
        self.assertEqual(result.backend_peak_rss_bytes, 61000000)
        self.assertEqual(result.backend_cpu_us, 1400)

    def test_rest_backend_classifies_transport_timeout(self):
        backend = model_backends.RestModelBackend(
            backend="cloud",
            url="https://model.example/infer",
            api_key="secret",
            timeout_s=2,
            model_version="19",
        )
        with patch.object(
            model_backends.urllib.request,
            "urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaises(model_backends.ModelBackendTimeoutError):
                backend._post_json({"features": []})

    def test_process_tree_sampler_sums_live_processes_and_ignores_exited_child(self):
        live_child = SimpleNamespace(
            pid=11,
            memory_info=lambda: SimpleNamespace(rss=200),
            cpu_times=lambda: SimpleNamespace(user=0.2, system=0.1),
        )
        exited_child = SimpleNamespace(
            pid=12,
            memory_info=lambda: (_ for _ in ()).throw(model_backends.psutil.NoSuchProcess(12)),
            cpu_times=lambda: SimpleNamespace(user=9.0, system=9.0),
        )
        root = SimpleNamespace(
            pid=10,
            children=lambda recursive: [live_child, exited_child],
            memory_info=lambda: SimpleNamespace(rss=100),
            cpu_times=lambda: SimpleNamespace(user=0.4, system=0.3),
        )
        sampler = model_backends.ProcessTreeSampler()

        with patch.object(model_backends.psutil, "Process", return_value=root):
            usage = sampler.sample(10)

        self.assertEqual(usage.rss_bytes, 300)
        self.assertAlmostEqual(usage.cpu_seconds, 1.0)
        self.assertEqual(sampler.peak_rss_bytes, 300)

    def test_remote_backend_rejects_non_http_url(self):
        with self.assertRaisesRegex(
            model_backends.ModelBackendError,
            "absolute http",
        ):
            model_backends.RestModelBackend(
                backend="edge",
                url="model.local/infer",
                api_key=None,
                timeout_s=2,
                model_version="19",
            )


class BleDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_source_rejects_onboard_firmware(self):
        config = SimpleNamespace(
            ble_device_id="AA:BB",
            ble_name="IMU-Raw-Stream",
        )
        source = imu_source.XiaoBleImuSource(config)
        discovered = [
            {
                "address": "AA:BB",
                "name": "IMU-Datastream",
                "firmware": "onboard_inference",
                "raw_compatible": False,
            }
        ]
        with patch.object(
            imu_source,
            "scan_ble_devices",
            new=AsyncMock(return_value=discovered),
        ):
            with self.assertRaisesRegex(
                imu_source.ImuSourceError,
                "continuous raw stream firmware",
            ):
                await source.connect()

    async def test_scan_distinguishes_raw_and_onboard_firmware(self):
        raw_device = SimpleNamespace(address="AA", name=None)
        onboard_device = SimpleNamespace(address="BB", name=None)
        raw_advertisement = SimpleNamespace(
            local_name="IMU-Raw-Stream",
            rssi=-55,
            service_uuids=[imu_source.RAW_SERVICE_UUID],
        )
        onboard_advertisement = SimpleNamespace(
            local_name="IMU-Datastream",
            rssi=-45,
            service_uuids=[imu_source.RAW_SERVICE_UUID],
        )
        scanner = SimpleNamespace(
            discover=AsyncMock(
                return_value={
                    "AA": (raw_device, raw_advertisement),
                    "BB": (onboard_device, onboard_advertisement),
                }
            )
        )
        with patch.object(imu_source, "BleakScanner", scanner):
            devices = await imu_source.scan_ble_devices(timeout=0.5)

        by_address = {device["address"]: device for device in devices}
        self.assertTrue(by_address["AA"]["raw_compatible"])
        self.assertFalse(by_address["BB"]["raw_compatible"])


class FakeStdin:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        return None


class FakeStdout:
    def __init__(self, response):
        self.response = response

    async def readexactly(self, size):
        if isinstance(self.response, BaseException):
            raise self.response
        assert len(self.response) == size
        return self.response


class FakeProcess:
    def __init__(self, response):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(response)
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -1


if __name__ == "__main__":
    unittest.main()
