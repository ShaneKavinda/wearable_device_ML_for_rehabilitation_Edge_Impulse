from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, IO, Optional, Sequence

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from imu_source import (  # noqa: E402
    GESTURE_MENU,
    ImuSourceError,
    XiaoBleImuSource,
    ble_runtime_status,
    scan_ble_devices,
)
from inference_protocol import (  # noqa: E402
    AXES_PER_SAMPLE,
    FEATURE_COUNT,
    FEATURE_AXES,
    FEATURE_UNITS,
    MODEL_DEPLOY_VERSION,
    MODEL_FREQUENCY_HZ,
    MODEL_LABELS,
    MODEL_PROJECT_ID,
    SAMPLE_COUNT,
    SAMPLE_INTERVAL_MS,
    pack_result_packet,
    unpack_result_packet,
)
from model_backends import (  # noqa: E402
    BACKEND_IDS,
    ModelBackendError,
    create_model_backend,
)

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

    class SerialException(Exception):
        pass


DEFAULT_BAUD = 115200
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8765
BLE_SOURCE_TYPE = XiaoBleImuSource.source_type
DEFAULT_SOURCE_TYPE = BLE_SOURCE_TYPE
LEGACY_SERIAL_SOURCE_TYPE = "edge_serial_beetle"
DEFAULT_BLE_NAME = "IMU-Raw-Stream"
DEFAULT_RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "edge_runner"
    / "build"
    / "edge_inference_runner.exe"
)
RESET_DELAY_SECONDS = 2.0
CONFIDENCE_THRESHOLD = 0.85
REST_EVENT_TYPES = {
    "state",
    "status",
    "session_start",
    "repetition_event",
    "inference_result",
    "session_summary",
    "metrics",
    "benchmark_saved",
    "error",
}


class ClientError(Exception):
    """Expected client-side error that should be shown without a traceback."""


@dataclass
class ClientStats:
    valid_count: int = 0
    invalid_count: int = 0


@dataclass(frozen=True)
class GestureChoice:
    selection: int
    label: str


@dataclass
class SourceConfig:
    source_type: str = DEFAULT_SOURCE_TYPE
    port: Optional[str] = None
    baud: int = DEFAULT_BAUD
    save_invalid: bool = False
    log_dir: Path = field(default_factory=lambda: Path("logs"))
    ble_device_id: Optional[str] = None
    ble_name: str = DEFAULT_BLE_NAME
    runner_path: Path = field(default_factory=lambda: DEFAULT_RUNNER_PATH)
    model_backend: str = "local"
    model_url: str = ""
    model_api_key: Optional[str] = None
    model_timeout_s: float = 10.0
    model_version: str = "19"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SourceConfig":
        raw_log_dir = payload.get("log_dir", "logs")
        source_type = str(payload.get("source_type", DEFAULT_SOURCE_TYPE))
        if source_type == "xiao_ble_edge":
            source_type = BLE_SOURCE_TYPE
        return SourceConfig(
            source_type=source_type,
            port=_optional_str(payload.get("port")),
            baud=_read_int(payload, "baud", DEFAULT_BAUD),
            save_invalid=bool(payload.get("save_invalid", False)),
            log_dir=Path(str(raw_log_dir)),
            ble_device_id=_optional_str(payload.get("ble_device_id")),
            ble_name=str(payload.get("ble_name", DEFAULT_BLE_NAME)).strip()
            or DEFAULT_BLE_NAME,
            runner_path=Path(
                _optional_str(payload.get("runner_path")) or str(DEFAULT_RUNNER_PATH)
            ),
            model_backend=str(payload.get("model_backend", "local")).strip().lower(),
            model_url=str(payload.get("model_url", "")).strip(),
            model_api_key=_optional_str(payload.get("model_api_key")),
            model_timeout_s=_bounded_float(
                payload.get("model_timeout_s", 10.0),
                "model_timeout_s",
                0.5,
                120.0,
            ),
            model_version=str(payload.get("model_version", "19")).strip() or "19",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "port": self.port,
            "baud": self.baud,
            "save_invalid": self.save_invalid,
            "log_dir": str(self.log_dir),
            "ble_device_id": self.ble_device_id,
            "ble_name": self.ble_name,
            "runner_path": str(self.runner_path),
            "model_backend": self.model_backend,
            "model_url": self.model_url,
            "model_api_key_set": bool(self.model_api_key),
            "model_timeout_s": self.model_timeout_s,
            "model_version": self.model_version,
        }


class JsonlLogger:
    def __init__(
        self,
        directory: Path,
        filename_factory: Callable[[], str] | None = None,
    ) -> None:
        self._directory = directory
        self._filename_factory = filename_factory or create_log_filename
        self._file: IO[str] | None = None
        self.path: Path | None = None

    def open(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self.path = self._directory / self._filename_factory()
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None

    def append_valid(self, received_at: str, data: Any) -> None:
        self._append({"received_at": received_at, "data": data})

    def append_invalid(self, received_at: str, line: str) -> None:
        self._append(
            {
                "received_at": received_at,
                "valid_json": False,
                "raw": line,
            }
        )

    def _append(self, record: dict[str, Any]) -> None:
        if self._file is None:
            self.open()
        assert self._file is not None
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()


class InferenceSource:
    source_type = "base"
    display_name = "Base source"
    uses_line_reader = True

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def read_line(self) -> str:
        raise NotImplementedError

    async def send_gesture_selection(self, choice: GestureChoice) -> None:
        raise NotImplementedError

    async def request_menu(self) -> None:
        return None


class BeetleSerialSource(InferenceSource):
    source_type = LEGACY_SERIAL_SOURCE_TYPE
    display_name = "Beetle RP2530 serial edge device"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self._serial_port: Any | None = None

    async def connect(self) -> None:
        if serial is None:
            raise ClientError(
                "pyserial is not installed. Install requirements first."
            )
        if not self.config.port:
            raise ClientError("Serial port is required for Beetle serial source.")

        def open_port() -> Any:
            return serial.Serial(
                port=self.config.port,
                baudrate=self.config.baud,
                timeout=1,
            )

        try:
            self._serial_port = await asyncio.to_thread(open_port)
        except SerialException as error:
            raise ClientError(f"Could not open {self.config.port}: {error}") from error

        await asyncio.sleep(RESET_DELAY_SECONDS)

    async def disconnect(self) -> None:
        port = self._serial_port
        self._serial_port = None
        if port is not None:
            await asyncio.to_thread(port.close)

    async def read_line(self) -> str:
        port = self._require_port()
        raw_bytes = await asyncio.to_thread(port.readline)
        return decode_serial_line(raw_bytes)

    async def send_gesture_selection(self, choice: GestureChoice) -> None:
        port = self._require_port()
        payload = f"{choice.selection}\n".encode("ascii")
        await asyncio.to_thread(port.write, payload)
        if hasattr(port, "flush"):
            await asyncio.to_thread(port.flush)

    async def request_menu(self) -> None:
        port = self._require_port()
        # The Beetle sketch prints the menu on startup and after an invalid
        # nonzero selection. This harmless invalid selection recovers the menu
        # when the API attaches after the startup print has already happened.
        await asyncio.to_thread(port.write, b"99\n")
        if hasattr(port, "flush"):
            await asyncio.to_thread(port.flush)

    def _require_port(self) -> Any:
        if self._serial_port is None:
            raise ClientError("Source is not connected.")
        return self._serial_port


SOURCE_FACTORIES: dict[str, type[InferenceSource]] = {
    BeetleSerialSource.source_type: BeetleSerialSource,
    XiaoBleImuSource.source_type: XiaoBleImuSource,
}


class BenchmarkStore:
    """Append-only benchmark storage with deterministic deduplication and summaries."""

    fieldnames = (
        "session_id",
        "deployment",
        "deployment_id",
        "gesture",
        "model_version",
        "window_id",
        "source_sequence",
        "repetition",
        "attempt",
        "correct",
        "confidence",
        "failed_attempts",
        "capture_ms",
        "inference_ms",
        "collect_ms",
        "device_span_ms",
        "end_to_end_ms",
        "non_capture_ms",
        "recorded_at",
    )

    def __init__(self, directory: Path) -> None:
        self.path = directory / "benchmark_records.jsonl"

    def append(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        record = self._normalize(payload)
        records = self.records()
        key = self._key(record)
        duplicate = next((item for item in records if self._key(item) == key), None)
        if duplicate is not None:
            return duplicate, False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record, True

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line in source:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
        return records

    def summary(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        records = [
            record
            for record in self.records()
            if all(
                value in (None, "") or str(record.get(key)) == str(value)
                for key, value in filters.items()
                if key in {"deployment", "deployment_id", "gesture", "model_version"}
            )
        ]
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for record in records:
            key = (
                str(record.get("deployment", record.get("deployment_id", ""))),
                str(record.get("gesture", "")),
                str(record.get("model_version", "")),
            )
            grouped.setdefault(key, []).append(record)

        groups = []
        for (deployment, gesture, model_version), items in sorted(grouped.items()):
            correct = sum(bool(item.get("correct")) for item in items)
            metrics = {
                name: _distribution(items, name)
                for name in (
                    "capture_ms",
                    "inference_ms",
                    "end_to_end_ms",
                    "non_capture_ms",
                )
            }
            groups.append(
                {
                    "deployment": deployment,
                    "gesture": gesture,
                    "model_version": model_version,
                    "count": len(items),
                    "accuracy": correct / len(items),
                    "failed_attempts": sum(
                        int(item.get("failed_attempts", 0)) for item in items
                    ),
                    **metrics,
                }
            )
        return {"record_count": len(records), "groups": groups}

    def csv_text(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=self.fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(self.records())
        return output.getvalue()

    @staticmethod
    def _key(record: dict[str, Any]) -> tuple[str, int]:
        return str(record["session_id"]), int(record["window_id"])

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        required = ("session_id", "deployment_id", "gesture", "window_id", "repetition")
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            raise ClientError(f"Benchmark record is missing: {', '.join(missing)}")
        deployment_id = _bounded_int(payload, "deployment_id", 0, 2)
        names = ("local", "edge", "cloud")
        record = {
            "session_id": str(payload["session_id"]),
            "deployment": str(payload.get("deployment", names[deployment_id])),
            "deployment_id": deployment_id,
            "gesture": str(payload["gesture"]),
            "model_version": str(payload.get("model_version", "19")),
            "window_id": _bounded_int(payload, "window_id", 0, 0xFFFFFFFF),
            "source_sequence": min(
                0xFFFFFFFF,
                max(0, _read_int(payload, "source_sequence", 0)),
            ),
            "repetition": _bounded_int(payload, "repetition", 1, 10),
            "attempt": max(1, int(payload.get("attempt", 1))),
            "correct": bool(payload.get("correct", False)),
            "confidence": _finite_float(payload.get("confidence", 0.0), "confidence"),
            "failed_attempts": max(0, int(payload.get("failed_attempts", 0))),
            "capture_ms": _finite_float(payload.get("capture_ms", 0.0), "capture_ms"),
            "inference_ms": _finite_float(payload.get("inference_ms", 0.0), "inference_ms"),
            "collect_ms": _finite_float(
                payload.get("collect_ms", payload.get("capture_ms", 0.0)),
                "collect_ms",
            ),
            "device_span_ms": _finite_float(
                payload.get("device_span_ms", 0.0),
                "device_span_ms",
            ),
            "end_to_end_ms": _finite_float(payload.get("end_to_end_ms", 0.0), "end_to_end_ms"),
            "non_capture_ms": _finite_float(payload.get("non_capture_ms", 0.0), "non_capture_ms"),
            "recorded_at": str(payload.get("recorded_at", utc_timestamp())),
        }
        return record


class LoggingService:
    """Single logging boundary for source events and benchmark records."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.event_log = JsonlLogger(directory)
        self.benchmarks = BenchmarkStore(directory)

    @property
    def active_path(self) -> Path | None:
        return self.event_log.path

    def start(self) -> None:
        self.event_log.open()

    def close(self) -> None:
        self.event_log.close()

    def append_result(self, received_at: str, data: Any) -> None:
        self.event_log.append_valid(received_at, data)

    def append_invalid(self, received_at: str, line: str) -> None:
        self.event_log.append_invalid(received_at, line)

    def record_benchmark(
        self,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        return self.benchmarks.append(payload)

    def benchmark_summary(
        self,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.benchmarks.summary(filters)

    def benchmark_csv(self) -> str:
        return self.benchmarks.csv_text()


def _distribution(records: list[dict[str, Any]], field_name: str) -> dict[str, float]:
    values = sorted(float(record.get(field_name, 0.0)) for record in records)
    index = max(0, math.ceil(0.95 * len(values)) - 1)
    return {"p50": statistics.median(values), "p95": values[index]}


class ApiHub:
    def __init__(
        self,
        source_factories: dict[str, type[InferenceSource]] | None = None,
    ) -> None:
        self.config = SourceConfig()
        self.stats = ClientStats()
        self.connected = False
        self.session_running = False
        self.gesture_menu: dict[str, Any] | None = None
        self.latest_result: dict[str, Any] | None = None
        self.latest_summary: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._source_factories = source_factories or SOURCE_FACTORIES
        self._source: InferenceSource | None = None
        self._model_backend: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._logging: LoggingService | None = None
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._capture_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._completed_captures: dict[int, tuple[bytes, dict[str, Any]]] = {}
        self._capture_tasks: dict[int, asyncio.Task[dict[str, Any]]] = {}
        self._capture_errors: dict[int, str] = {}

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.connected:
            raise ClientError("Disconnect the active source before reconfiguring.")
        next_config = SourceConfig.from_payload(payload)
        if next_config.source_type not in self._source_factories:
            raise ClientError(f"Unsupported source type: {next_config.source_type}")
        if next_config.model_backend not in BACKEND_IDS:
            raise ClientError(
                "model_backend must be one of: local, edge, cloud."
            )
        if (
            next_config.source_type == BLE_SOURCE_TYPE
            and next_config.model_backend != "local"
            and not next_config.model_url
        ):
            raise ClientError(
                "model_url is required for edge and cloud deployment."
            )
        self.config = next_config
        return self.state_snapshot()

    async def connect(self) -> dict[str, Any]:
        # BLE discovery and runner warm-up can take several seconds. Serialize
        # setup so duplicate requests cannot overwrite shared cleanup state.
        async with self._connect_lock:
            return await self._connect_unlocked()

    async def _connect_unlocked(self) -> dict[str, Any]:
        if self.connected:
            return self.state_snapshot()

        source_type = self.config.source_type
        source_factory = self._source_factories.get(source_type)
        if source_factory is None:
            raise ClientError(f"Unsupported source type: {source_type}")

        source = source_factory(self.config)
        try:
            model_backend = (
                create_model_backend(self.config)
                if hasattr(source, "capture_samples")
                else None
            )
        except ModelBackendError as error:
            self.last_error = str(error)
            raise ClientError(str(error)) from error
        logging_service = LoggingService(self.config.log_dir)
        logging_service.start()
        self._source = source
        self._model_backend = model_backend
        self._logging = logging_service
        self.stats = ClientStats()
        self.gesture_menu = (
            dict(GESTURE_MENU)
            if not getattr(source, "uses_line_reader", True)
            else None
        )
        self.latest_result = None
        self.latest_summary = None
        self.last_error = None

        try:
            await source.connect()
            if model_backend is not None:
                await model_backend.start()
        except Exception as error:
            try:
                await source.disconnect()
            except Exception:
                pass
            if model_backend is not None:
                try:
                    await model_backend.close()
                except Exception:
                    pass
            logging_service.close()
            if self._logging is logging_service:
                self._logging = None
            if self._source is source:
                self._source = None
            if self._model_backend is model_backend:
                self._model_backend = None
            self.last_error = str(error)
            if isinstance(error, (ImuSourceError, ModelBackendError)):
                raise ClientError(str(error)) from error
            raise

        self.connected = True
        if getattr(source, "uses_line_reader", True):
            self._reader_task = asyncio.create_task(self._read_loop())
        await source.request_menu()
        await self._broadcast_state()
        return self.state_snapshot()

    async def disconnect(self) -> dict[str, Any]:
        self.session_running = False
        self.connected = False

        capture_tasks = list(self._capture_tasks.items())
        self._capture_tasks.clear()
        for window_id, capture_task in capture_tasks:
            self._capture_errors[window_id] = "Capture stopped because the source disconnected."
            capture_task.cancel()
        if capture_tasks:
            await asyncio.gather(
                *(capture_task for _, capture_task in capture_tasks),
                return_exceptions=True,
            )

        task = self._reader_task
        self._reader_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        source = self._source
        self._source = None
        if source is not None:
            await source.disconnect()

        model_backend = self._model_backend
        self._model_backend = None
        if model_backend is not None:
            await model_backend.close()

        if self._logging is not None:
            self._logging.close()
            self._logging = None

        await self._broadcast_state()
        return self.state_snapshot()

    async def start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.connected or self._source is None:
            raise ClientError("Connect a source before starting a session.")
        if self.gesture_menu is None:
            raise ClientError("No gesture menu has been received from the source yet.")

        requested = payload.get("gesture", payload.get("selection"))
        if requested is None:
            raise ClientError("Session start requires a gesture label or selection.")
        choice = resolve_gesture_selection(self.gesture_menu, str(requested))
        if choice is None:
            raise ClientError(f"Gesture not found in current menu: {requested}")

        await self._source.send_gesture_selection(choice)
        self.session_running = True
        await self._broadcast_state()
        return {"selected": {"selection": choice.selection, "label": choice.label}}

    async def request_menu(self) -> dict[str, Any]:
        if not self.connected or self._source is None:
            raise ClientError("Connect a source before requesting a gesture menu.")
        await self._source.request_menu()
        return self.state_snapshot()

    async def capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.connected or self._source is None:
            raise ClientError("Connect the IMU source before capture.")
        capture_samples = getattr(self._source, "capture_samples", None)
        capture_window = getattr(self._source, "capture_window", None)
        if capture_samples is None and capture_window is None:
            raise ClientError("The active source does not support single-window capture.")
        if self.gesture_menu is None:
            raise ClientError("Gesture menu is unavailable.")

        window_id = _bounded_int(payload, "window_id", 0, 0xFFFFFFFF)
        repetition = _bounded_int(payload, "repetition", 1, 10)
        requested = payload.get("gesture", payload.get("selection"))
        if requested is None:
            raise ClientError("Capture requires a gesture label or selection.")
        choice = resolve_gesture_selection(self.gesture_menu, str(requested))
        if choice is None:
            raise ClientError(f"Gesture not found in current menu: {requested}")

        async with self._capture_lock:
            try:
                if capture_samples is not None:
                    model_backend = self._model_backend
                    if model_backend is None:
                        raise ModelBackendError("No model backend is connected.")
                    captured = await capture_samples()
                    inference = await model_backend.classify(
                        window_id,
                        captured.features(),
                    )
                    predicted_class = max(
                        range(len(inference.scores)),
                        key=inference.scores.__getitem__,
                    )
                    confidence = inference.scores[predicted_class]
                    predicted = MODEL_LABELS[predicted_class]
                    trusted = confidence >= CONFIDENCE_THRESHOLD
                    correct = predicted.casefold() == choice.label.casefold()
                    packet = pack_result_packet(
                        deployment=model_backend.deployment_id,
                        window_id=window_id,
                        source_sequence=captured.source_sequence,
                        inference_us=inference.inference_us,
                        confidence=confidence,
                        repetition=repetition,
                        predicted_class=predicted_class,
                        ok=True,
                        trusted=trusted,
                        correct=correct,
                    )
                    detail = {
                        "type": "inference_result",
                        "device_id": "pc_rest_xiao_ble",
                        "deployment": inference.backend,
                        "deployment_id": model_backend.deployment_id,
                        "model_version": inference.model_version,
                        "repetition": repetition,
                        "window_id": window_id,
                        "target": choice.label,
                        "sample_count": SAMPLE_COUNT,
                        "axes_per_sample": AXES_PER_SAMPLE,
                        "input_frame_size": FEATURE_COUNT,
                        "sample_interval_ms": SAMPLE_INTERVAL_MS,
                        "mean_interval_ms": captured.mean_interval_ms,
                        "source_sequence": captured.source_sequence,
                        "ok": True,
                        "label": predicted,
                        "predicted": predicted,
                        "correct": correct,
                        "trusted": trusted,
                        "confidence": confidence,
                        "confidence_threshold": CONFIDENCE_THRESHOLD,
                        "collect_ms": captured.capture_ms,
                        "device_span_ms": captured.device_span_ms,
                        "inference_ms": inference.inference_us / 1000.0,
                        "timing_ms": {"wall": inference.inference_us / 1000.0},
                        "scores": dict(zip(MODEL_LABELS, inference.scores)),
                        "error": None,
                    }
                else:
                    packet, detail = await capture_window(
                        window_id=window_id,
                        selection=choice.selection,
                        repetition=repetition,
                        target=choice.label,
                    )
            except (ImuSourceError, ModelBackendError) as error:
                self.last_error = str(error)
                await self._broadcast(
                    {
                        "type": "error",
                        "received_at": utc_timestamp(),
                        "data": {
                            "message": str(error),
                            "window_id": window_id,
                            "repetition": repetition,
                        },
                    }
                )
                raise ClientError(str(error)) from error

        received_at = utc_timestamp()
        self.stats.valid_count += 1
        self.latest_result = detail
        self._completed_captures[window_id] = (packet, detail)
        while len(self._completed_captures) > 100:
            self._completed_captures.pop(next(iter(self._completed_captures)))
        if self._logging is not None:
            self._logging.append_result(received_at, detail)
        await self._broadcast(
            {
                "type": "inference_result",
                "received_at": received_at,
                "data": sanitize_web_data(detail),
            }
        )
        await self._broadcast_metrics(received_at)
        return detail

    def enqueue_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Start one idempotent capture and return without holding HTTP open."""
        if not self.connected or self._source is None:
            raise ClientError("Connect the IMU source before capture.")
        if (
            getattr(self._source, "capture_samples", None) is None
            and getattr(self._source, "capture_window", None) is None
        ):
            raise ClientError("The active source does not support single-window capture.")
        if self.gesture_menu is None:
            raise ClientError("Gesture menu is unavailable.")

        window_id = _bounded_int(payload, "window_id", 0, 0xFFFFFFFF)
        _bounded_int(payload, "repetition", 1, 10)
        requested = payload.get("gesture", payload.get("selection"))
        if requested is None:
            raise ClientError("Capture requires a gesture label or selection.")
        if resolve_gesture_selection(self.gesture_menu, str(requested)) is None:
            raise ClientError(f"Gesture not found in current menu: {requested}")

        if window_id in self._completed_captures:
            return {"status": "complete", "window_id": window_id}
        task = self._capture_tasks.get(window_id)
        if task is not None and not task.done():
            return {"status": "pending", "window_id": window_id}

        self._capture_errors.pop(window_id, None)
        task = asyncio.create_task(self.capture(dict(payload)))
        self._capture_tasks[window_id] = task
        task.add_done_callback(
            lambda completed_task, capture_id=window_id: self._capture_finished(
                capture_id,
                completed_task,
            )
        )
        return {"status": "pending", "window_id": window_id}

    def _capture_finished(
        self,
        window_id: int,
        task: asyncio.Task[dict[str, Any]],
    ) -> None:
        if self._capture_tasks.get(window_id) is task:
            self._capture_tasks.pop(window_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            self._capture_errors.setdefault(window_id, "Capture was cancelled.")
        except Exception as error:
            self._capture_errors[window_id] = str(error)

    def capture_status(self, window_id: int) -> dict[str, Any]:
        completed = self._completed_captures.get(window_id)
        if completed is not None:
            packet, detail = completed
            return {
                "status": "complete",
                "window_id": window_id,
                "packet_base64": base64.b64encode(packet).decode("ascii"),
                "detail": sanitize_web_data(detail),
            }
        error = self._capture_errors.get(window_id)
        if error is not None:
            return {"status": "error", "window_id": window_id, "message": error}
        task = self._capture_tasks.get(window_id)
        if task is not None and not task.done():
            return {"status": "pending", "window_id": window_id}
        return {"status": "not_found", "window_id": window_id}

    def completed_capture(self, window_id: int) -> tuple[bytes, dict[str, Any]]:
        try:
            return self._completed_captures[window_id]
        except KeyError:
            raise ClientError(
                f"Capture result {window_id} is not available."
            ) from None

    async def record_benchmark(self, payload: dict[str, Any]) -> dict[str, Any]:
        logging_service = self._logging or LoggingService(self.config.log_dir)
        record, created = logging_service.record_benchmark(payload)
        envelope = {
            "type": "benchmark_saved",
            "received_at": utc_timestamp(),
            "data": {"created": created, "record": record},
        }
        await self._broadcast(envelope)
        return envelope["data"]

    def benchmark_summary(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        logging_service = self._logging or LoggingService(self.config.log_dir)
        return logging_service.benchmark_summary(filters)

    def benchmark_csv(self) -> str:
        logging_service = self._logging or LoggingService(self.config.log_dir)
        return logging_service.benchmark_csv()

    async def stop_session(self) -> dict[str, Any]:
        await self.disconnect()
        return self.state_snapshot()

    def events_after(self, after_id: int = 0, limit: int = 100) -> dict[str, Any]:
        events = [
            event
            for event in self._events
            if int(event.get("event_id", 0)) > after_id
        ][: max(1, min(limit, 200))]
        return {
            "events": events,
            "latest_event_id": self._next_event_id - 1,
        }

    def available_sources(self) -> list[dict[str, str]]:
        return [
            {
                "type": source_type,
                "name": source_factory.display_name,
            }
            for source_type, source_factory in self._source_factories.items()
        ]

    def logs(self) -> list[dict[str, Any]]:
        log_dir = self.config.log_dir
        if not log_dir.exists():
            return []
        logs = []
        for path in sorted(log_dir.glob("*.jsonl"), reverse=True):
            logs.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "size_bytes": path.stat().st_size,
                    "modified_at": datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                }
            )
        return logs

    def state_snapshot(self) -> dict[str, Any]:
        log_file = self._logging.active_path if self._logging is not None else None
        return {
            "config": self.config.to_dict(),
            "connected": self.connected,
            "session_running": self.session_running,
            "gesture_menu": self.gesture_menu,
            "latest_result": self.latest_result,
            "latest_summary": self.latest_summary,
            "stats": {
                "valid_count": self.stats.valid_count,
                "invalid_count": self.stats.invalid_count,
            },
            "log_file": str(log_file.resolve()) if log_file is not None else None,
            "last_error": self.last_error,
        }

    def state_envelope(self) -> dict[str, Any]:
        return {
            "type": "state",
            "received_at": utc_timestamp(),
            "data": self.state_snapshot(),
        }

    async def _read_loop(self) -> None:
        while self.connected and self._source is not None:
            try:
                line = await self._source.read_line()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = str(error)
                await self._broadcast(
                    {
                        "type": "error",
                        "received_at": utc_timestamp(),
                        "data": {"message": str(error)},
                    }
                )
                await self.disconnect()
                break

            if not line:
                continue
            await self.handle_serial_line(line)

    async def handle_serial_line(self, line: str) -> dict[str, Any] | None:
        received_at = utc_timestamp()
        logging_service = self._logging

        try:
            json_data = json.loads(line)
        except json.JSONDecodeError:
            self.stats.invalid_count += 1
            if logging_service is not None and self.config.save_invalid:
                logging_service.append_invalid(received_at, line)
            await self._broadcast_metrics(received_at)
            return None

        if not isinstance(json_data, dict):
            self.stats.invalid_count += 1
            if logging_service is not None and self.config.save_invalid:
                logging_service.append_invalid(received_at, line)
            await self._broadcast_metrics(received_at)
            return None

        self.stats.valid_count += 1
        if logging_service is not None:
            logging_service.append_result(received_at, json_data)

        event_type = str(json_data.get("type", ""))
        if event_type == "gesture_menu":
            self.gesture_menu = json_data
            await self._broadcast_state(received_at)
        elif event_type == "session_start":
            self.session_running = True
        elif event_type == "inference_result":
            self.latest_result = json_data
        elif event_type == "session_summary":
            self.latest_summary = json_data
            self.session_running = False
        elif event_type == "status":
            await self._broadcast_state(received_at)

        envelope = to_rest_envelope(json_data, received_at)
        if envelope is not None:
            await self._broadcast(envelope)
        await self._broadcast_metrics(received_at)
        return json_data

    async def _broadcast_state(self, received_at: str | None = None) -> None:
        await self._broadcast(
            {
                "type": "state",
                "received_at": received_at or utc_timestamp(),
                "data": self.state_snapshot(),
            }
        )

    async def _broadcast_metrics(self, received_at: str | None = None) -> None:
        log_file = self._logging.active_path if self._logging is not None else None
        await self._broadcast(
            {
                "type": "metrics",
                "received_at": received_at or utc_timestamp(),
                "data": {
                    "valid_count": self.stats.valid_count,
                    "invalid_count": self.stats.invalid_count,
                    "connected": self.connected,
                    "session_running": self.session_running,
                    "log_file": str(log_file.resolve())
                    if log_file is not None
                    else None,
                },
            }
        )

    async def _broadcast(self, envelope: dict[str, Any]) -> None:
        if envelope.get("type") not in REST_EVENT_TYPES:
            return
        event = {**envelope, "event_id": self._next_event_id}
        self._next_event_id += 1
        self._events.append(event)
        if len(self._events) > 200:
            del self._events[: len(self._events) - 200]


def create_log_filename() -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"pc_event_log_{timestamp}.jsonl"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_serial_line(raw_bytes: bytes) -> str:
    return raw_bytes.decode("utf-8", errors="replace").strip()


def get_gesture_options(menu_data: dict[str, Any]) -> list[GestureChoice]:
    options = []

    for item in menu_data.get("gestures", []):
        if not isinstance(item, dict):
            continue

        try:
            selection = int(item["selection"])
            label = str(item["label"])
        except (KeyError, TypeError, ValueError):
            continue

        options.append(GestureChoice(selection=selection, label=label))

    return options


def resolve_gesture_selection(
    menu_data: dict[str, Any],
    requested: str,
) -> Optional[GestureChoice]:
    requested_text = requested.strip()
    if not requested_text:
        return None

    options = get_gesture_options(menu_data)

    try:
        requested_number = int(requested_text)
    except ValueError:
        requested_number = None

    if requested_number is not None:
        for option in options:
            if option.selection == requested_number:
                return option
        return None

    requested_label = requested_text.casefold()
    for option in options:
        if option.label.casefold() == requested_label:
            return option

    return None


def to_rest_envelope(
    device_event: dict[str, Any],
    received_at: str,
) -> dict[str, Any] | None:
    event_type = str(device_event.get("type", ""))

    if event_type == "status":
        return {
            "type": "status",
            "received_at": received_at,
            "data": {
                "device_id": device_event.get("device_id"),
                "status_event": device_event.get("event"),
                "message": device_event.get("message"),
            },
        }

    if event_type not in {
        "session_start",
        "repetition_event",
        "inference_result",
        "session_summary",
    }:
        return None

    return {
        "type": event_type,
        "received_at": received_at,
        "data": sanitize_web_data(device_event),
    }


def sanitize_web_data(device_event: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "type",
        "device_id",
        "deployment",
        "deployment_id",
        "model_version",
        "target",
        "repetitions",
        "repetition",
        "window_id",
        "event",
        "sample_count",
        "axes_per_sample",
        "input_frame_size",
        "sample_interval_ms",
        "source_sequence",
        "ok",
        "label",
        "predicted",
        "correct",
        "trusted",
        "accuracy",
        "accuracy_percent",
        "confidence",
        "confidence_threshold",
        "inference_ms",
        "collect_ms",
        "device_span_ms",
        "timing_ms",
        "memory_bytes",
        "scores",
        "error_code",
        "error",
        "total_repetitions",
        "correct_count",
        "pass_count",
        "pass_rate",
        "pass_rate_percent",
        "uncertain_count",
        "avg_pass_confidence",
        "avg_inference_ms",
        "min_free_memory_bytes",
    }
    return {key: value for key, value in device_event.items() if key in allowed_keys}


def list_serial_port_info() -> list[dict[str, Any]]:
    if list_ports is None:
        return []
    return [
        {
            "device": item.device,
            "description": item.description,
            "hwid": item.hwid,
        }
        for item in list_ports.comports()
    ]


def write_jsonl_record(log_file: IO[str], record: dict[str, Any]) -> None:
    log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    log_file.flush()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_int(payload: dict[str, Any], key: str, fallback: int) -> int:
    value = payload.get(key, fallback)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ClientError(f"{key} must be an integer.") from None


def _bounded_int(payload: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    if key not in payload:
        raise ClientError(f"{key} is required.")
    value = _read_int(payload, key, minimum)
    if value < minimum or value > maximum:
        raise ClientError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _finite_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ClientError(f"{field_name} must be a number.") from None
    if not math.isfinite(number) or number < 0:
        raise ClientError(f"{field_name} must be a finite non-negative number.")
    return number


def _bounded_float(
    value: Any,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    number = _finite_float(value, field_name)
    if number < minimum or number > maximum:
        raise ClientError(
            f"{field_name} must be between {minimum} and {maximum}."
        )
    return number


def create_app(hub: ApiHub | None = None) -> Any:
    try:
        from fastapi import Body, FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse, Response
    except ImportError as error:
        raise ClientError(
            "FastAPI server dependencies are missing. Run: py -m pip install -r requirements.txt"
        ) from error

    globals()["Response"] = Response

    api_hub = hub or ApiHub()
    app = FastAPI(title="IMU Rehab API Hub")
    app.state.hub = api_hub

    @app.get("/", response_class=HTMLResponse)
    async def web_gui() -> str:
        return web_gui_html()

    @app.get("/mobile", response_class=HTMLResponse)
    async def mobile_session() -> str:
        return mobile_session_html()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "time": utc_timestamp(),
            "transport": "rest",
            "phone_ui": "/mobile",
            "model_backends": list(BACKEND_IDS),
            "model_contract": {
                "project_id": MODEL_PROJECT_ID,
                "deployment_version": MODEL_DEPLOY_VERSION,
                "sample_count": SAMPLE_COUNT,
                "frequency_hz": MODEL_FREQUENCY_HZ,
                "sample_interval_ms": SAMPLE_INTERVAL_MS,
                "axes": list(FEATURE_AXES),
                "units": list(FEATURE_UNITS),
                "feature_count": FEATURE_COUNT,
            },
            "ble": ble_runtime_status(),
        }

    @app.get("/api/sources")
    async def sources() -> dict[str, Any]:
        return {"sources": api_hub.available_sources()}

    @app.get("/api/serial/ports")
    async def serial_ports() -> dict[str, Any]:
        return {"ports": list_serial_port_info()}

    @app.get("/api/ble/devices")
    async def ble_devices(timeout: float = 5.0) -> dict[str, Any]:
        try:
            return {"devices": await scan_ble_devices(timeout=max(0.5, min(timeout, 15.0)))}
        except ImuSourceError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return api_hub.state_snapshot()

    @app.get("/api/events")
    async def events(after_id: int = 0, limit: int = 100) -> dict[str, Any]:
        return api_hub.events_after(max(0, after_id), limit)

    @app.put("/api/config")
    async def config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return api_hub.configure(payload)
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/source/connect")
    async def connect() -> dict[str, Any]:
        try:
            return await api_hub.connect()
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/source/disconnect")
    async def disconnect() -> dict[str, Any]:
        return await api_hub.disconnect()

    @app.post("/api/session/start")
    async def start_session(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            return await api_hub.start_session(payload)
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/source/request-menu")
    async def request_menu() -> dict[str, Any]:
        try:
            return await api_hub.request_menu()
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/session/stop")
    async def stop_session() -> dict[str, Any]:
        return await api_hub.stop_session()

    @app.post("/api/capture")
    async def capture_http(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        """Run a capture with an HTTP response as a durable WS fallback."""
        try:
            detail = await api_hub.capture(payload)
            window_id = _bounded_int(payload, "window_id", 0, 0xFFFFFFFF)
            packet, _ = api_hub.completed_capture(window_id)
            return {
                "status": "complete",
                "window_id": window_id,
                "packet_base64": base64.b64encode(packet).decode("ascii"),
                "detail": sanitize_web_data(detail),
            }
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/captures", status_code=202)
    async def enqueue_capture(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            return api_hub.enqueue_capture(payload)
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/captures/{window_id}")
    async def completed_capture(window_id: int) -> Any:
        """Poll an asynchronous capture using short-lived HTTP requests."""
        snapshot = api_hub.capture_status(window_id)
        status = snapshot["status"]
        if status == "complete":
            return snapshot
        if status == "pending":
            return JSONResponse(status_code=202, content=snapshot)
        if status == "error":
            return JSONResponse(status_code=409, content=snapshot)
        return JSONResponse(status_code=404, content=snapshot)

    @app.post("/api/benchmarks/records")
    async def benchmark_record(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            return await api_hub.record_benchmark(payload)
        except ClientError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/logs")
    async def logs() -> dict[str, Any]:
        return {"logs": api_hub.logs()}

    @app.get("/api/benchmarks/summary")
    async def benchmark_summary(
        deployment: str | None = None,
        gesture: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        return api_hub.benchmark_summary(
            {
                "deployment": deployment,
                "gesture": gesture,
                "model_version": model_version,
            }
        )

    @app.get("/api/benchmarks/export.csv")
    async def benchmark_export() -> Response:
        return Response(
            api_hub.benchmark_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=imu_benchmarks.csv"},
        )

    return app


def web_gui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IMU Rehab API Hub</title>
  <style>
    :root { color-scheme: light; font-family: Arial, sans-serif; }
    body { margin: 0; background: #f5f7f8; color: #172026; }
    header { background: #0f766e; color: white; padding: 16px 24px; display: flex; justify-content: space-between; gap: 12px; align-items: center; }
    header a { color: #0f766e; background: white; text-decoration: none; padding: 9px 12px; border-radius: 6px; font-weight: 700; }
    main { display: grid; gap: 16px; padding: 16px; max-width: 1120px; margin: 0 auto; }
    section { background: white; border: 1px solid #d8dee4; border-radius: 8px; padding: 16px; }
    h1, h2 { margin: 0 0 12px; }
    label { display: grid; gap: 6px; margin-bottom: 10px; font-weight: 600; }
    input, select, button { font: inherit; padding: 9px 10px; border-radius: 6px; border: 1px solid #b6c2ca; }
    select:disabled { color: #52636f; background: #edf2f4; }
    button { border: 0; background: #0f766e; color: white; cursor: pointer; }
    button.secondary { background: #40515d; }
    button.warn { background: #b42318; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; }
    .metric { display: grid; gap: 4px; padding: 10px; background: #eef3f4; border-radius: 6px; }
    .metric span:first-child { color: #52636f; font-size: 12px; text-transform: uppercase; }
    .metric span:last-child { overflow-wrap: anywhere; }
    pre { white-space: pre-wrap; max-height: 260px; overflow: auto; background: #111827; color: #e5e7eb; padding: 12px; border-radius: 6px; }
  </style>
</head>
<body>
  <header><h1>IMU Rehab API Hub</h1><a href="/mobile">Open Mobile Session</a></header>
  <main>
    <section>
      <h2>Source Setup</h2>
      <div class="grid">
        <label>Source
          <select id="sourceType"></select>
        </label>
        <label>Serial Port
          <input id="port" placeholder="COM13">
        </label>
        <label>Baud
          <input id="baud" type="number" value="115200">
        </label>
        <label>Log Directory
          <input id="logDir" value="logs">
        </label>
        <label>BLE Device Address
          <input id="bleDeviceId" placeholder="scan, select, or enter an address">
        </label>
        <label>BLE Device Name
          <input id="bleName" value="IMU-Raw-Stream">
        </label>
        <label>Discovered BLE Device
          <select id="bleDevices" onchange="chooseBleDevice()">
            <option value="">Press Scan BLE</option>
          </select>
        </label>
        <label>Model deployment
          <select id="modelBackend">
            <option value="local">Local PC runner</option>
            <option value="edge">Remote edge REST model</option>
            <option value="cloud">Cloud REST model</option>
          </select>
        </label>
        <label>Model REST URL (edge/cloud)
          <input id="modelUrl" placeholder="https://host/infer">
        </label>
        <label>Model API key (optional)
          <input id="modelApiKey" type="password" autocomplete="off">
        </label>
        <label>Model timeout (seconds)
          <input id="modelTimeout" type="number" min="0.5" max="120" step="0.5" value="10">
        </label>
        <label>Model version
          <input id="modelVersion" value="19">
        </label>
        <label>Windows Runner (local deployment)
          <input id="runnerPath" placeholder=".../edge_inference_runner.exe">
        </label>
      </div>
      <label><input id="saveInvalid" type="checkbox"> Save invalid serial lines</label>
      <div class="row">
        <button onclick="saveConfig()">Save Config</button>
        <button id="connectButton" onclick="connectSource()">Connect</button>
        <button class="secondary" onclick="disconnectSource()">Disconnect</button>
        <button class="secondary" onclick="refreshAll()">Refresh</button>
        <button id="scanBleButton" class="secondary" onclick="scanBle()">Scan BLE</button>
      </div>
    </section>
    <section>
      <h2>Live Status</h2>
      <div class="grid">
        <div class="metric"><span>Connected</span><span id="connected">-</span></div>
        <div class="metric"><span>Source</span><span id="source">-</span></div>
        <div class="metric"><span>Model Deployment</span><span id="activeModel">-</span></div>
        <div class="metric"><span>Serial Port</span><span id="serialPort">-</span></div>
        <div class="metric"><span>Valid Records</span><span id="valid">0</span></div>
        <div class="metric"><span>Invalid Records</span><span id="invalid">0</span></div>
        <div class="metric"><span>Log File</span><span id="logFile">-</span></div>
      </div>
    </section>
    <section>
      <h2>Benchmark Summary</h2>
      <div class="grid">
        <label>Deployment filter
          <input id="benchmarkDeployment" placeholder="local, edge, or cloud">
        </label>
        <label>Gesture filter
          <input id="benchmarkGesture" placeholder="Flexion">
        </label>
        <label>Model version filter
          <input id="benchmarkModel" placeholder="19">
        </label>
      </div>
      <div class="row">
        <button class="secondary" onclick="refreshBenchmarks()">Refresh Summary</button>
        <button onclick="location.href='/api/benchmarks/export.csv'">Export CSV</button>
      </div>
      <pre id="benchmarks"></pre>
    </section>
    <section>
      <h2>Event Stream</h2>
      <pre id="events"></pre>
    </section>
  </main>
  <script>
    let lastEventId = 0;
    const $ = (id) => document.getElementById(id);
    const configFieldIds = [
      'sourceType', 'port', 'baud', 'logDir', 'bleDeviceId', 'bleName',
      'runnerPath', 'modelBackend', 'modelUrl', 'modelApiKey',
      'modelTimeout', 'modelVersion', 'saveInvalid'
    ];
    const dirtyConfigFields = new Set();
    let configEditRevision = 0;

    function markConfigDirty(id) {
      dirtyConfigFields.add(id);
      configEditRevision += 1;
    }

    for (const id of configFieldIds) {
      $(id).addEventListener('input', () => markConfigDirty(id));
    }

    function renderConfigValue(id, value) {
      if (dirtyConfigFields.has(id)) return;
      $(id).value = value;
    }

    function renderConfigChecked(id, checked) {
      if (dirtyConfigFields.has(id)) return;
      $(id).checked = checked;
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: {'content-type': 'application/json'},
        ...options
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || response.statusText);
      }
      return response.json();
    }

    async function refreshAll() {
      const sources = await api('/api/sources');
      const selectedSource = $('sourceType').value;
      $('sourceType').innerHTML = sources.sources.map(
        (source) => `<option value="${source.type}">${source.name}</option>`
      ).join('');
      if (dirtyConfigFields.has('sourceType')) {
        $('sourceType').value = selectedSource;
      }
      renderState(await api('/api/state'));
      await refreshBenchmarks();
    }

    async function saveConfig() {
      const submittedRevision = configEditRevision;
      const state = await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify({
          source_type: $('sourceType').value,
          port: $('port').value,
          baud: Number($('baud').value),
          save_invalid: $('saveInvalid').checked,
          log_dir: $('logDir').value,
          ble_device_id: $('bleDeviceId').value,
          ble_name: $('bleName').value,
          runner_path: $('runnerPath').value,
          model_backend: $('modelBackend').value,
          model_url: $('modelUrl').value,
          model_api_key: $('modelApiKey').value,
          model_timeout_s: Number($('modelTimeout').value),
          model_version: $('modelVersion').value
        })
      });
      if (configEditRevision === submittedRevision) {
        dirtyConfigFields.clear();
      }
      renderState(state);
    }

    async function connectSource() {
      const button = $('connectButton');
      const selectedDevice = $('bleDevices').selectedOptions[0];
      if (
        $('sourceType').value === 'xiao_ble_imu' &&
        selectedDevice?.dataset.firmware === 'onboard_inference'
      ) {
        const message = 'This XIAO has onboard-inference firmware. Flash imu_raw_datastream_xiao_ble.ino before using the PC REST coordinator.';
        appendEvent({type: 'connection_error', data: {message}});
        alert(message);
        return;
      }
      button.disabled = true;
      button.textContent = 'Connecting...';
      try {
        await saveConfig();
        renderState(await api('/api/source/connect', {method: 'POST'}));
      } catch (error) {
        appendEvent({type: 'connection_error', data: {message: error.message}});
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = 'Connect';
      }
    }

    async function disconnectSource() {
      renderState(await api('/api/source/disconnect', {method: 'POST'}));
    }

    async function scanBle() {
      const button = $('scanBleButton');
      button.disabled = true;
      button.textContent = 'Scanning...';
      try {
        const result = await api('/api/ble/devices?timeout=8');
        const selector = $('bleDevices');
        selector.replaceChildren();
        for (const device of result.devices) {
          const option = document.createElement('option');
          option.value = device.address;
          option.dataset.name = device.name;
          option.dataset.firmware = device.firmware;
          const signal = device.rssi == null ? '' : `, ${device.rssi} dBm`;
          let marker = '';
          if (device.raw_compatible) marker = 'Raw IMU XIAO: ';
          else if (device.firmware === 'onboard_inference') marker = 'Onboard firmware (reflash for PC inference): ';
          else if (device.likely_xiao) marker = 'Possible XIAO: ';
          option.textContent = `${marker}${device.name} (${device.address}${signal})`;
          selector.appendChild(option);
        }
        if (!result.devices.length) {
          const option = document.createElement('option');
          option.value = '';
          option.textContent = 'No BLE devices found';
          selector.appendChild(option);
        } else {
          const wantedName = $('bleName').value.toLocaleLowerCase();
          const selectedIndex = result.devices.findIndex(
            (device) => device.raw_compatible || device.name.toLocaleLowerCase() === wantedName
          );
          selector.selectedIndex = selectedIndex >= 0 ? selectedIndex : 0;
          chooseBleDevice();
        }
        appendEvent({type: 'ble_scan', data: result.devices});
      } catch (error) {
        appendEvent({type: 'ble_scan_error', data: {message: error.message}});
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = 'Scan BLE';
      }
    }

    function chooseBleDevice() {
      const selector = $('bleDevices');
      const option = selector.options[selector.selectedIndex];
      if (option?.value) {
        $('bleDeviceId').value = option.value;
        markConfigDirty('bleDeviceId');
      }
    }

    async function refreshBenchmarks() {
      const query = new URLSearchParams();
      if ($('benchmarkDeployment').value) query.set('deployment', $('benchmarkDeployment').value);
      if ($('benchmarkGesture').value) query.set('gesture', $('benchmarkGesture').value);
      if ($('benchmarkModel').value) query.set('model_version', $('benchmarkModel').value);
      $('benchmarks').textContent = JSON.stringify(
        await api(`/api/benchmarks/summary?${query}`), null, 2
      );
    }

    function renderState(state) {
      $('connected').textContent = state.connected ? 'yes' : 'no';
      $('source').textContent = state.config?.source_type || '-';
      $('activeModel').textContent = state.config?.model_backend || '-';
      renderConfigValue(
        'sourceType',
        state.config?.source_type || $('sourceType').value
      );
      $('serialPort').textContent = state.config?.port || '-';
      $('valid').textContent = state.stats?.valid_count ?? 0;
      $('invalid').textContent = state.stats?.invalid_count ?? 0;
      $('logFile').textContent = state.log_file || '-';
      renderConfigValue('port', state.config?.port || $('port').value);
      renderConfigValue('baud', state.config?.baud || 115200);
      renderConfigValue('logDir', state.config?.log_dir || 'logs');
      renderConfigChecked('saveInvalid', Boolean(state.config?.save_invalid));
      renderConfigValue('bleDeviceId', state.config?.ble_device_id || '');
      renderConfigValue('bleName', state.config?.ble_name || 'IMU-Raw-Stream');
      renderConfigValue('runnerPath', state.config?.runner_path || '');
      renderConfigValue('modelBackend', state.config?.model_backend || 'local');
      renderConfigValue('modelUrl', state.config?.model_url || '');
      renderConfigValue('modelTimeout', state.config?.model_timeout_s || 10);
      renderConfigValue('modelVersion', state.config?.model_version || '19');
    }

    function appendEvent(envelope) {
      $('events').textContent = `${JSON.stringify(envelope, null, 2)}\n\n${$('events').textContent}`;
      if (envelope.type === 'state') {
        renderState(envelope.data);
      } else if (envelope.type === 'status') {
        // Status envelopes are informational; full state arrives separately.
      } else if (envelope.type === 'metrics') {
        $('valid').textContent = envelope.data.valid_count;
        $('invalid').textContent = envelope.data.invalid_count;
        $('logFile').textContent = envelope.data.log_file || '-';
      }
    }

    async function pollDashboard() {
      const state = await api('/api/state');
      renderState(state);
      const feed = await api(`/api/events?after_id=${lastEventId}`);
      for (const event of feed.events) {
        appendEvent(event);
        lastEventId = Math.max(lastEventId, event.event_id || 0);
      }
    }

    refreshAll().catch((error) => alert(error.message));
    setInterval(() => pollDashboard().catch(() => {}), 1000);
  </script>
</body>
</html>"""


def mobile_session_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>IMU Motion Session</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #f1f5f4; color: #172026; }
    header { padding: max(18px, env(safe-area-inset-top)) 18px 18px; background: #0f766e; color: white; }
    header h1 { margin: 0; font-size: 24px; }
    header p { margin: 6px 0 0; opacity: .9; }
    main { width: min(100%, 620px); margin: 0 auto; padding: 16px 16px max(24px, env(safe-area-inset-bottom)); display: grid; gap: 14px; }
    .card { background: white; border: 1px solid #d7e0de; border-radius: 14px; padding: 16px; box-shadow: 0 3px 14px #15342e12; }
    label { display: grid; gap: 7px; font-weight: 700; }
    select, button { width: 100%; min-height: 48px; border-radius: 10px; font: inherit; font-weight: 700; }
    select { padding: 10px; border: 1px solid #aab9b6; background: white; }
    button { border: 0; padding: 11px 14px; color: white; background: #0f766e; }
    button.secondary { background: #52636f; }
    button.danger { background: #b42318; }
    button:disabled { opacity: .5; }
    .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
    .actions button:first-child:last-child { grid-column: 1 / -1; }
    .phase { min-height: 190px; display: grid; place-content: center; text-align: center; transition: background .15s, color .15s; }
    .phase.green { background: #16a34a; color: white; border-color: #15803d; }
    .phase.error { background: #fff0ed; border-color: #f4aaa0; color: #8a1c12; }
    #phaseTitle { margin: 0; font-size: clamp(36px, 12vw, 70px); line-height: 1; }
    #phaseDetail { margin: 12px 0 0; font-size: 17px; }
    .progress { height: 12px; background: #dce7e4; border-radius: 999px; overflow: hidden; }
    .progress > div { height: 100%; width: 0; background: #0f766e; transition: width .2s; }
    .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; }
    .metric { background: #edf4f2; border-radius: 9px; padding: 9px; text-align: center; }
    .metric strong { display: block; font-size: 20px; }
    .metric span { color: #52636f; font-size: 12px; }
    #results { margin: 0; white-space: pre-wrap; font: 14px/1.5 ui-monospace, monospace; }
    .small { color: #52636f; font-size: 13px; }
    a { color: #0f766e; }
    [hidden] { display: none !important; }
  </style>
</head>
<body>
  <header>
    <h1>IMU Motion Session</h1>
    <p>PC-coordinated local, edge, or cloud inference over REST</p>
  </header>
  <main>
    <section class="card">
      <label for="motion">Choose motion
        <select id="motion">
          <option value="1">Flexion</option>
          <option value="2">Extension</option>
          <option value="3">Pronation</option>
          <option value="4">Supination</option>
          <option value="5">Radial Deviation</option>
          <option value="6">Ulnar Deviation</option>
        </select>
      </label>
      <div class="actions">
        <button id="startButton">Start 10 repetitions</button>
        <button id="stopButton" class="danger" hidden>Stop</button>
        <button id="retryButton" hidden>Retry repetition</button>
      </div>
      <p class="small">The PC dashboard must already show the XIAO raw BLE IMU and model backend as connected. <a href="/">Open setup</a></p>
    </section>

    <section id="phaseCard" class="card phase">
      <div>
        <h2 id="phaseTitle">Ready</h2>
        <p id="phaseDetail">Choose a motion and start</p>
      </div>
    </section>

    <section class="card">
      <div class="progress"><div id="progressBar"></div></div>
      <div class="metrics">
        <div class="metric"><strong id="completed">0</strong><span>completed</span></div>
        <div class="metric"><strong id="remaining">10</strong><span>remaining</span></div>
        <div class="metric"><strong id="failed">0</strong><span>failed</span></div>
      </div>
    </section>

    <section class="card">
      <h2>Results</h2>
      <pre id="results">No results yet.</pre>
    </section>
  </main>
  <script>
    const motions = new Map([
      [1, 'Flexion'], [2, 'Extension'], [3, 'Pronation'],
      [4, 'Supination'], [5, 'Radial Deviation'], [6, 'Ulnar Deviation']
    ]);
    const $ = (id) => document.getElementById(id);
    let running = false;
    let stopRequested = false;
    let decisionResolver = null;
    let windowCounter = Date.now() % 4294967296;
    let completed = 0;
    let failed = 0;
    let resultLines = [];

    function nextWindowId() {
      windowCounter = (windowCounter + 1) % 4294967296;
      return windowCounter;
    }

    async function request(path, options = {}, timeoutMs = 2000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(path, {
          headers: {'content-type': 'application/json'},
          cache: 'no-store',
          signal: controller.signal,
          ...options
        });
        const data = await response.json().catch(() => ({}));
        return {response, data};
      } finally {
        clearTimeout(timer);
      }
    }

    async function api(path, options = {}, timeoutMs = 2000) {
      const {response, data} = await request(path, options, timeoutMs);
      if (!response.ok) throw new Error(data.detail || data.message || response.statusText);
      return data;
    }

    async function delay(ms) {
      const until = performance.now() + ms;
      while (!stopRequested && performance.now() < until) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(100, until - performance.now())));
      }
    }

    function setPhase(title, detail, kind = '') {
      $('phaseTitle').textContent = title;
      $('phaseDetail').textContent = detail;
      $('phaseCard').className = `card phase ${kind}`.trim();
    }

    function updateProgress() {
      $('completed').textContent = completed;
      $('remaining').textContent = 10 - completed;
      $('failed').textContent = failed;
      $('progressBar').style.width = `${completed * 10}%`;
    }

    async function queueCapture(payload) {
      let lastError = new Error('Capture could not be queued.');
      for (let attempt = 0; attempt < 4 && !stopRequested; attempt++) {
        try {
          const {response, data} = await request('/api/captures', {
            method: 'POST', body: JSON.stringify(payload)
          });
          if (response.ok) return data;
          throw new Error(data.detail || data.message || response.statusText);
        } catch (error) {
          lastError = error;
          await delay(200);
        }
      }
      throw lastError;
    }

    async function pollCapture(payload) {
      const deadline = performance.now() + 15000;
      while (!stopRequested && performance.now() < deadline) {
        try {
          const {response, data} = await request(`/api/captures/${payload.window_id}`);
          if (response.status === 200 && data.status === 'complete') return data;
          if (response.status === 409 || data.status === 'error') {
            const failure = new Error(data.message || 'Capture failed.');
            failure.captureFailure = true;
            throw failure;
          }
          if (response.status === 404) await queueCapture(payload);
        } catch (error) {
          if (error.captureFailure) throw error;
        }
        await delay(150);
      }
      if (stopRequested) throw new Error('Session stopped.');
      throw new Error('No result was available after 15 seconds of HTTP polling.');
    }

    function showResult(repetition, detail, endToEndMs) {
      const confidence = Number(detail.confidence || 0) * 100;
      resultLines.unshift(
        `${repetition}. ${detail.predicted || detail.label || 'Unknown'} | ` +
        `${confidence.toFixed(1)}% | ${detail.correct ? 'correct' : 'incorrect'} | ` +
        `${endToEndMs.toFixed(1)} ms end-to-end`
      );
      $('results').textContent = resultLines.join('\\n');
    }

    async function saveBenchmark({sessionId, gesture, windowId, repetition, attempt, detail, endToEndMs}) {
      const captureMs = Number(detail.collect_ms || 0);
      const inferenceMs = Number(detail.inference_ms || 0);
      const deployment = detail.deployment || 'local';
      const deploymentId = {local: 0, edge: 1, cloud: 2}[deployment] ?? 0;
      await api('/api/benchmarks/records', {
        method: 'POST',
        body: JSON.stringify({
          type: 'benchmark_record', session_id: sessionId,
          deployment, deployment_id: deploymentId, gesture,
          model_version: detail.model_version || 'unknown', window_id: windowId, repetition, attempt,
          source_sequence: Number(detail.source_sequence || 0),
          correct: Boolean(detail.correct), confidence: Number(detail.confidence || 0),
          failed_attempts: failed, capture_ms: captureMs,
          collect_ms: captureMs, device_span_ms: Number(detail.device_span_ms || 0),
          inference_ms: inferenceMs, end_to_end_ms: endToEndMs,
          non_capture_ms: Math.max(0, endToEndMs - captureMs - inferenceMs)
        })
      }, 3000);
    }

    function askRetry(message) {
      setPhase('Try again', message, 'error');
      $('retryButton').hidden = false;
      return new Promise((resolve) => { decisionResolver = resolve; });
    }

    function finishRun(message) {
      running = false;
      decisionResolver = null;
      $('motion').disabled = false;
      $('startButton').disabled = false;
      $('stopButton').hidden = true;
      $('retryButton').hidden = true;
      if (completed === 10) setPhase('Complete', message);
      else if (stopRequested) setPhase('Stopped', message);
    }

    async function startRun() {
      if (running) return;
      try {
        const state = await api('/api/state');
        if (!state.connected || state.config?.source_type !== 'xiao_ble_imu') {
          throw new Error('Connect the XIAO raw BLE IMU source from the PC setup page first.');
        }
      } catch (error) {
        setPhase('Not connected', error.message, 'error');
        return;
      }

      running = true;
      stopRequested = false;
      completed = 0;
      failed = 0;
      resultLines = [];
      $('results').textContent = 'Waiting for first result.';
      $('motion').disabled = true;
      $('startButton').disabled = true;
      $('stopButton').hidden = false;
      updateProgress();

      const selection = Number($('motion').value);
      const gesture = motions.get(selection);
      const sessionId = `${new Date().toISOString()}-${Math.random().toString(16).slice(2)}`;
      let attempt = 0;

      while (!stopRequested && completed < 10) {
        const repetition = completed + 1;
        attempt += 1;
        try {
          setPhase('Get ready', `${gesture} - repetition ${repetition} of 10`);
          await delay(1000);
          for (let count = 3; count >= 1 && !stopRequested; count--) {
            setPhase(String(count), `Repetition ${repetition} of 10`);
            await delay(1000);
          }
          if (stopRequested) break;

          const windowId = nextWindowId();
          const payload = {
            type: 'capture', window_id: windowId, selection,
            repetition, gesture
          };
          setPhase('GO', `Perform ${gesture} now`, 'green');
          const greenStarted = performance.now();
          await queueCapture(payload);
          const snapshot = await pollCapture(payload);
          const resultAt = performance.now();
          const endToEndMs = resultAt - greenStarted;
          const detail = snapshot.detail || {};

          completed += 1;
          showResult(repetition, detail, endToEndMs);
          updateProgress();
          saveBenchmark({
            sessionId, gesture, windowId, repetition, attempt, detail, endToEndMs
          }).catch(() => {});
          attempt = 0;

          if (completed < 10) {
            setPhase('Rest', `${completed} of 10 complete`);
            await delay(2000);
          }
        } catch (error) {
          if (stopRequested) break;
          failed += 1;
          updateProgress();
          const retry = await askRetry(error.message || String(error));
          $('retryButton').hidden = true;
          decisionResolver = null;
          if (!retry) {
            stopRequested = true;
            break;
          }
        }
      }

      finishRun(completed === 10 ? 'Ten valid repetitions recorded.' : 'Session ended.');
    }

    $('startButton').addEventListener('click', () => startRun());
    $('retryButton').addEventListener('click', () => {
      if (decisionResolver) decisionResolver(true);
    });
    $('stopButton').addEventListener('click', () => {
      stopRequested = true;
      if (decisionResolver) decisionResolver(false);
    });
  </script>
</body>
</html>"""


async def run_once(args: argparse.Namespace) -> None:
    config = SourceConfig(
        source_type=LEGACY_SERIAL_SOURCE_TYPE,
        port=args.port,
        baud=args.baud,
        save_invalid=args.save_invalid,
        log_dir=args.log_dir,
    )
    hub = ApiHub()
    hub.configure(config.to_dict())
    await hub.connect()
    print(f"Connected to {args.port} at {args.baud} baud")
    print("Waiting for gesture menu...")

    try:
        while hub.gesture_menu is None:
            await asyncio.sleep(0.1)

        choice = resolve_gesture_selection(hub.gesture_menu, args.gesture)
        if choice is None:
            raise ClientError(f"Gesture not found in current menu: {args.gesture}")

        await hub.start_session({"selection": choice.selection})
        print(f"Selected gesture: {choice.selection} - {choice.label}")

        while hub.latest_summary is None:
            await asyncio.sleep(0.25)

        print(json.dumps(hub.latest_summary, ensure_ascii=False, indent=2))
    finally:
        await hub.disconnect()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IMU rehab PC API hub")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the REST API and web GUI")
    serve_parser.add_argument("--host", default=DEFAULT_API_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_API_PORT)

    run_once_parser = subparsers.add_parser(
        "run-once",
        help="Run one Beetle serial session from the terminal",
    )
    run_once_parser.add_argument("--port", required=True, help="Serial port, for example COM13")
    run_once_parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    run_once_parser.add_argument("--gesture", required=True)
    run_once_parser.add_argument("--save-invalid", action="store_true")
    run_once_parser.add_argument("--log-dir", type=Path, default=Path("logs"))

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "serve"
        args.host = DEFAULT_API_HOST
        args.port = DEFAULT_API_PORT
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        if args.command == "serve":
            try:
                import uvicorn
            except ImportError as error:
                raise ClientError(
                    "uvicorn is missing. Run: py -m pip install -r requirements.txt"
                ) from error

            uvicorn.run(
                "datastream_client:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                reload=False,
            )
            return

        if args.command == "run-once":
            asyncio.run(run_once(args))
            return

        raise ClientError(f"Unknown command: {args.command}")
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except ClientError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
