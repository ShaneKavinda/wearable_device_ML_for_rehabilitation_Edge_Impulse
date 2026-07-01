from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, IO, Optional, Sequence

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
DEFAULT_SOURCE_TYPE = "edge_serial_beetle"
RESET_DELAY_SECONDS = 2.0
MOBILE_EVENT_TYPES = {
    "state",
    "status",
    "session_start",
    "repetition_event",
    "inference_result",
    "session_summary",
    "metrics",
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

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SourceConfig":
        raw_log_dir = payload.get("log_dir", "logs")
        return SourceConfig(
            source_type=str(payload.get("source_type", DEFAULT_SOURCE_TYPE)),
            port=_optional_str(payload.get("port")),
            baud=_read_int(payload, "baud", DEFAULT_BAUD),
            save_invalid=bool(payload.get("save_invalid", False)),
            log_dir=Path(str(raw_log_dir)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "port": self.port,
            "baud": self.baud,
            "save_invalid": self.save_invalid,
            "log_dir": str(self.log_dir),
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
    source_type = DEFAULT_SOURCE_TYPE
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
}


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
        self._reader_task: asyncio.Task[None] | None = None
        self._logger: JsonlLogger | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.connected:
            raise ClientError("Disconnect the active source before reconfiguring.")
        next_config = SourceConfig.from_payload(payload)
        if next_config.source_type not in self._source_factories:
            raise ClientError(f"Unsupported source type: {next_config.source_type}")
        self.config = next_config
        return self.state_snapshot()

    async def connect(self) -> dict[str, Any]:
        if self.connected:
            return self.state_snapshot()

        source_type = self.config.source_type
        source_factory = self._source_factories.get(source_type)
        if source_factory is None:
            raise ClientError(f"Unsupported source type: {source_type}")

        self._source = source_factory(self.config)
        self._logger = JsonlLogger(self.config.log_dir)
        self._logger.open()
        self.stats = ClientStats()
        self.gesture_menu = None
        self.latest_result = None
        self.latest_summary = None
        self.last_error = None

        try:
            await self._source.connect()
        except Exception:
            self._logger.close()
            self._logger = None
            self._source = None
            raise

        self.connected = True
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._source.request_menu()
        await self._broadcast_state()
        return self.state_snapshot()

    async def disconnect(self) -> dict[str, Any]:
        self.session_running = False
        self.connected = False

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

        if self._logger is not None:
            self._logger.close()
            self._logger = None

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

    async def stop_session(self) -> dict[str, Any]:
        await self.disconnect()
        return self.state_snapshot()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        queue.put_nowait(self.mobile_state_envelope())
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

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
        log_file = self._logger.path if self._logger is not None else None
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

    def mobile_state_envelope(self) -> dict[str, Any]:
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
        logger = self._logger

        try:
            json_data = json.loads(line)
        except json.JSONDecodeError:
            self.stats.invalid_count += 1
            if logger is not None and self.config.save_invalid:
                logger.append_invalid(received_at, line)
            await self._broadcast_metrics(received_at)
            return None

        if not isinstance(json_data, dict):
            self.stats.invalid_count += 1
            if logger is not None and self.config.save_invalid:
                logger.append_invalid(received_at, line)
            await self._broadcast_metrics(received_at)
            return None

        self.stats.valid_count += 1
        if logger is not None:
            logger.append_valid(received_at, json_data)

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

        envelope = to_mobile_envelope(json_data, received_at)
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
        log_file = self._logger.path if self._logger is not None else None
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
        if envelope.get("type") not in MOBILE_EVENT_TYPES:
            return
        stale = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self.unsubscribe(queue)


def create_log_filename() -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"arduino_log_{timestamp}.jsonl"


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


def to_mobile_envelope(
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
        "data": sanitize_mobile_data(device_event),
    }


def sanitize_mobile_data(device_event: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "type",
        "device_id",
        "target",
        "repetitions",
        "repetition",
        "window_id",
        "event",
        "sample_count",
        "axes_per_sample",
        "input_frame_size",
        "sample_interval_ms",
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


def create_app(hub: ApiHub | None = None) -> Any:
    try:
        from fastapi import Body, FastAPI, HTTPException, WebSocket
        from fastapi.responses import HTMLResponse
        from starlette.websockets import WebSocketDisconnect
    except ImportError as error:
        raise ClientError(
            "FastAPI server dependencies are missing. Run: py -m pip install -r requirements.txt"
        ) from error

    # Endpoint annotations are postponed by `from __future__ import annotations`.
    # FastAPI resolves them from module globals, so make the locally imported
    # WebSocket class visible there before defining routes.
    globals()["WebSocket"] = WebSocket

    api_hub = hub or ApiHub()
    app = FastAPI(title="IMU Rehab API Hub")
    app.state.hub = api_hub

    @app.get("/", response_class=HTMLResponse)
    async def web_gui() -> str:
        return web_gui_html()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "time": utc_timestamp()}

    @app.get("/api/sources")
    async def sources() -> dict[str, Any]:
        return {"sources": api_hub.available_sources()}

    @app.get("/api/serial/ports")
    async def serial_ports() -> dict[str, Any]:
        return {"ports": list_serial_port_info()}

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return api_hub.state_snapshot()

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

    @app.get("/api/logs")
    async def logs() -> dict[str, Any]:
        return {"logs": api_hub.logs()}

    @app.websocket("/ws/results")
    async def results_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = api_hub.subscribe()
        try:
            while True:
                envelope = await queue.get()
                await websocket.send_json(envelope)
        except WebSocketDisconnect:
            pass
        finally:
            api_hub.unsubscribe(queue)

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
    header { background: #0f766e; color: white; padding: 16px 24px; }
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
  <header><h1>IMU Rehab API Hub</h1></header>
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
      </div>
      <label><input id="saveInvalid" type="checkbox"> Save invalid serial lines</label>
      <div class="row">
        <button onclick="saveConfig()">Save Config</button>
        <button onclick="connectSource()">Connect</button>
        <button class="secondary" onclick="disconnectSource()">Disconnect</button>
        <button class="secondary" onclick="refreshAll()">Refresh</button>
      </div>
    </section>
    <section>
      <h2>Live Status</h2>
      <div class="grid">
        <div class="metric"><span>Connected</span><span id="connected">-</span></div>
        <div class="metric"><span>Source</span><span id="source">-</span></div>
        <div class="metric"><span>Serial Port</span><span id="serialPort">-</span></div>
        <div class="metric"><span>Valid Records</span><span id="valid">0</span></div>
        <div class="metric"><span>Invalid Records</span><span id="invalid">0</span></div>
        <div class="metric"><span>Log File</span><span id="logFile">-</span></div>
      </div>
    </section>
    <section>
      <h2>Event Stream</h2>
      <pre id="events"></pre>
    </section>
  </main>
  <script>
    let socket;
    const $ = (id) => document.getElementById(id);

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
      $('sourceType').innerHTML = sources.sources.map(
        (source) => `<option value="${source.type}">${source.name}</option>`
      ).join('');
      renderState(await api('/api/state'));
    }

    async function saveConfig() {
      const state = await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify({
          source_type: $('sourceType').value,
          port: $('port').value,
          baud: Number($('baud').value),
          save_invalid: $('saveInvalid').checked,
          log_dir: $('logDir').value
        })
      });
      renderState(state);
    }

    async function connectSource() {
      await saveConfig();
      renderState(await api('/api/source/connect', {method: 'POST'}));
    }

    async function disconnectSource() {
      renderState(await api('/api/source/disconnect', {method: 'POST'}));
    }

    function renderState(state) {
      $('connected').textContent = state.connected ? 'yes' : 'no';
      $('source').textContent = state.config?.source_type || '-';
      $('serialPort').textContent = state.config?.port || '-';
      $('valid').textContent = state.stats?.valid_count ?? 0;
      $('invalid').textContent = state.stats?.invalid_count ?? 0;
      $('logFile').textContent = state.log_file || '-';
      $('port').value = state.config?.port || $('port').value;
      $('baud').value = state.config?.baud || 115200;
      $('logDir').value = state.config?.log_dir || 'logs';
      $('saveInvalid').checked = Boolean(state.config?.save_invalid);
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

    function connectSocket() {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${scheme}://${location.host}/ws/results`);
      socket.onmessage = (event) => appendEvent(JSON.parse(event.data));
      socket.onclose = () => setTimeout(connectSocket, 1500);
    }

    refreshAll().catch((error) => alert(error.message));
    connectSocket();
  </script>
</body>
</html>"""


async def run_once(args: argparse.Namespace) -> None:
    config = SourceConfig(
        source_type=DEFAULT_SOURCE_TYPE,
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

    serve_parser = subparsers.add_parser("serve", help="Run the REST/WebSocket API")
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
