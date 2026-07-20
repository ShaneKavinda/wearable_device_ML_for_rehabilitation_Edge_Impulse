from __future__ import annotations

import asyncio
import importlib.metadata
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any

try:
    from .inference_protocol import (
        ACCEL_LSB_PER_G,
        EXPECTED_DEVICE_SPAN_MS,
        FEATURE_COUNT,
        GYRO_LSB_PER_DPS,
        SAMPLE_COUNT,
        SAMPLE_INTERVAL_US,
    )
except ImportError:
    from inference_protocol import (
        ACCEL_LSB_PER_G,
        EXPECTED_DEVICE_SPAN_MS,
        FEATURE_COUNT,
        GYRO_LSB_PER_DPS,
        SAMPLE_COUNT,
        SAMPLE_INTERVAL_US,
    )


_BLEAK_IMPORT_ERROR: str | None = None
try:
    from bleak import BleakClient, BleakScanner
except (ImportError, OSError) as error:
    BleakClient = None
    BleakScanner = None
    _BLEAK_IMPORT_ERROR = f"{type(error).__name__}: {error}"


RAW_DEVICE_NAME = "IMU-Raw-Stream"
ONBOARD_DEVICE_NAME = "IMU-Datastream"
RAW_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RAW_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RAW_PACKET = struct.Struct("<II6h")
CAPTURE_TIMEOUT_SECONDS = 3.0
MEAN_INTERVAL_TOLERANCE = 0.025
INDIVIDUAL_INTERVAL_TOLERANCE = 0.35

GESTURE_MENU = {
    "type": "gesture_menu",
    "device_id": "xiao_esp32s3_raw_ble",
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


class ImuSourceError(RuntimeError):
    pass


class SequenceGapError(ImuSourceError):
    pass


class SamplingContractError(ImuSourceError):
    pass


@dataclass(frozen=True)
class RawImuPacket:
    sequence: int
    timestamp_us: int
    ax: int
    ay: int
    az: int
    gx: int
    gy: int
    gz: int

    @classmethod
    def decode(cls, data: bytes) -> "RawImuPacket":
        if len(data) != RAW_PACKET.size:
            raise ImuSourceError(
                f"Raw IMU notification must be {RAW_PACKET.size} bytes, got {len(data)}."
            )
        return cls(*RAW_PACKET.unpack(data))

    def features(self) -> tuple[float, float, float, float, float, float]:
        # The training CSV stored acceleration in g and angular velocity in
        # degrees/second. The Edge Impulse raw DSP block has scale_axes=1.0,
        # so inference must preserve those units exactly.
        return (
            self.ax / ACCEL_LSB_PER_G,
            self.ay / ACCEL_LSB_PER_G,
            self.az / ACCEL_LSB_PER_G,
            self.gx / GYRO_LSB_PER_DPS,
            self.gy / GYRO_LSB_PER_DPS,
            self.gz / GYRO_LSB_PER_DPS,
        )


@dataclass(frozen=True)
class CapturedWindow:
    packets: tuple[RawImuPacket, ...]
    capture_ms: float
    device_span_ms: float

    @property
    def source_sequence(self) -> int:
        return self.packets[-1].sequence

    def features(self) -> list[float]:
        values: list[float] = []
        for packet in self.packets:
            values.extend(packet.features())
        if len(values) != FEATURE_COUNT:
            raise SamplingContractError(
                f"Model requires {FEATURE_COUNT} features, got {len(values)}."
            )
        return values

    @property
    def mean_interval_ms(self) -> float:
        return self.device_span_ms / (len(self.packets) - 1)


def validate_model_window(packets: list[RawImuPacket]) -> tuple[float, float]:
    """Validate the device-side timestamps against deployment 19's cadence."""
    if len(packets) != SAMPLE_COUNT:
        raise SamplingContractError(
            f"Model requires {SAMPLE_COUNT} samples, got {len(packets)}."
        )

    deltas_us = [
        (current.timestamp_us - previous.timestamp_us) & 0xFFFFFFFF
        for previous, current in zip(packets, packets[1:])
    ]
    device_span_ms = sum(deltas_us) / 1000.0
    mean_interval_us = sum(deltas_us) / len(deltas_us)
    mean_error = abs(mean_interval_us - SAMPLE_INTERVAL_US) / SAMPLE_INTERVAL_US
    if mean_error > MEAN_INTERVAL_TOLERANCE:
        actual_hz = 1_000_000.0 / mean_interval_us if mean_interval_us else 0.0
        raise SamplingContractError(
            "IMU cadence does not match deployment 19: "
            f"expected 16.5 Hz ({SAMPLE_INTERVAL_US / 1000.0:.3f} ms), "
            f"received {actual_hz:.3f} Hz ({mean_interval_us / 1000.0:.3f} ms)."
        )

    maximum_interval_error = max(
        abs(delta - SAMPLE_INTERVAL_US) / SAMPLE_INTERVAL_US
        for delta in deltas_us
    )
    if maximum_interval_error > INDIVIDUAL_INTERVAL_TOLERANCE:
        raise SamplingContractError(
            "IMU timestamps contain a sampling stall; retry this repetition."
        )

    # This comparison is deliberately redundant with the mean check: it makes
    # the expected first-to-last span explicit for rollover and regression tests.
    span_error = abs(device_span_ms - EXPECTED_DEVICE_SPAN_MS)
    if span_error > EXPECTED_DEVICE_SPAN_MS * MEAN_INTERVAL_TOLERANCE:
        raise SamplingContractError(
            "IMU window duration does not match the model-native two-second window."
        )
    return device_span_ms, mean_interval_us / 1000.0


class XiaoBleImuSource:
    """Continuous BLE IMU source with green-window sample capture only."""

    source_type = "xiao_ble_imu"
    display_name = "XIAO raw BLE IMU"
    uses_line_reader = False

    def __init__(self, config: Any) -> None:
        self.config = config
        self._client: Any | None = None
        self._capture_future: asyncio.Future[CapturedWindow] | None = None
        self._capture_packets: list[RawImuPacket] = []
        self._expected_sequence: int | None = None
        self._capture_started_ns = 0
        self._capture_lock = asyncio.Lock()
        self.notification_count = 0
        self.dropped_sample_count = 0
        self.invalid_packet_count = 0

    async def connect(self) -> None:
        if BleakClient is None or BleakScanner is None:
            raise ImuSourceError(_bleak_unavailable_message())

        address = self.config.ble_device_id
        devices = await scan_ble_devices(timeout=5.0)
        if not address:
            match = next(
                (
                    item
                    for item in devices
                    if item["name"].casefold() == self.config.ble_name.casefold()
                    or item["raw_compatible"]
                ),
                None,
            )
            if match is None:
                raise ImuSourceError(
                    f"BLE device named {self.config.ble_name!r} was not found."
                )
            address = match["address"]
        else:
            match = next(
                (
                    item
                    for item in devices
                    if item["address"].casefold() == str(address).casefold()
                ),
                None,
            )

        if match is not None and match["firmware"] == "onboard_inference":
            raise ImuSourceError(
                f"{match['name']} ({address}) is running onboard-inference firmware. "
                "The PC REST architecture requires the continuous raw stream firmware "
                "advertised as IMU-Raw-Stream."
            )

        self._client = BleakClient(address, disconnected_callback=self._on_disconnect)
        try:
            await self._client.connect()
            await self._client.start_notify(RAW_TX_UUID, self._on_notification)
        except Exception as error:
            await self.disconnect()
            raise ImuSourceError(
                f"Could not connect to BLE device {address}: "
                f"{type(error).__name__}: {error}. Confirm the XIAO is powered, "
                "advertising IMU-Raw-Stream, and not connected to another client."
            ) from error

    async def disconnect(self) -> None:
        future = self._capture_future
        self._capture_future = None
        if future is not None and not future.done():
            future.set_exception(ImuSourceError("XIAO BLE source disconnected."))

        client = self._client
        self._client = None
        if client is not None:
            try:
                if client.is_connected:
                    await client.stop_notify(RAW_TX_UUID)
                    await client.disconnect()
            except Exception:
                pass

    async def read_line(self) -> str:
        await asyncio.sleep(3600)
        return ""

    async def request_menu(self) -> None:
        return None

    async def send_gesture_selection(self, choice: Any) -> None:
        del choice
        raise ImuSourceError("Raw BLE uses capture_samples, not legacy sessions.")

    async def capture_samples(self) -> CapturedWindow:
        async with self._capture_lock:
            if self._client is None or not self._client.is_connected:
                raise ImuSourceError("XIAO raw BLE source is not connected.")

            loop = asyncio.get_running_loop()
            future: asyncio.Future[CapturedWindow] = loop.create_future()
            self._capture_future = future
            self._capture_packets = []
            self._expected_sequence = None
            self._capture_started_ns = time.perf_counter_ns()
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=CAPTURE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                raise ImuSourceError(
                    f"Timed out waiting for {SAMPLE_COUNT} contiguous IMU samples."
                ) from None
            finally:
                self._capture_future = None
                self._capture_packets = []
                self._expected_sequence = None

    def _on_disconnect(self, client: Any) -> None:
        del client
        future = self._capture_future
        if future is not None and not future.done():
            future.set_exception(ImuSourceError("XIAO BLE source disconnected."))

    def _on_notification(self, characteristic: Any, data: bytearray) -> None:
        del characteristic
        self.notification_count += 1
        future = self._capture_future
        if future is None or future.done():
            return
        try:
            packet = RawImuPacket.decode(bytes(data))
        except ImuSourceError as error:
            self.invalid_packet_count += 1
            future.set_exception(error)
            return

        expected = self._expected_sequence
        if expected is not None and packet.sequence != expected:
            missing = (packet.sequence - expected) & 0xFFFFFFFF
            self.dropped_sample_count += missing
            future.set_exception(
                SequenceGapError(
                    f"IMU sequence gap: expected {expected}, received {packet.sequence}."
                )
            )
            return

        self._expected_sequence = (packet.sequence + 1) & 0xFFFFFFFF
        self._capture_packets.append(packet)
        if len(self._capture_packets) == SAMPLE_COUNT:
            capture_ms = (
                time.perf_counter_ns() - self._capture_started_ns
            ) / 1_000_000.0
            try:
                device_span_ms, _ = validate_model_window(self._capture_packets)
            except SamplingContractError as error:
                future.set_exception(error)
                return
            future.set_result(
                CapturedWindow(
                    packets=tuple(self._capture_packets),
                    capture_ms=capture_ms,
                    device_span_ms=device_span_ms,
                )
            )


def _bleak_unavailable_message() -> str:
    detail = f" Import error: {_BLEAK_IMPORT_ERROR}." if _BLEAK_IMPORT_ERROR else ""
    return (
        "BLE support is unavailable in this Python environment. Run "
        f"{sys.executable!r} -m pip install -r requirements.txt, then restart the "
        f"PC client.{detail}"
    )


def ble_runtime_status() -> dict[str, Any]:
    try:
        version = importlib.metadata.version("bleak")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "available": BleakClient is not None and BleakScanner is not None,
        "version": version,
        "python_executable": sys.executable,
        "import_error": _BLEAK_IMPORT_ERROR,
    }


async def scan_ble_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    if BleakScanner is None:
        raise ImuSourceError(_bleak_unavailable_message())
    try:
        discovered = await BleakScanner.discover(
            timeout=timeout,
            return_adv=True,
            scanning_mode="active",
        )
    except Exception as error:
        raise ImuSourceError(
            f"BLE scan failed: {type(error).__name__}: {error}. Confirm Windows "
            "Bluetooth is enabled and no other scan is already running."
        ) from error

    devices: list[dict[str, Any]] = []
    values = discovered.values() if isinstance(discovered, dict) else discovered
    for item in values:
        if isinstance(item, tuple):
            device, advertisement = item
            name = advertisement.local_name or device.name or "Unnamed BLE device"
            rssi = advertisement.rssi
            service_uuids = list(advertisement.service_uuids or [])
        else:
            device = item
            name = device.name or "Unnamed BLE device"
            rssi = getattr(device, "rssi", None)
            service_uuids = []
        normalized_services = [str(value).lower() for value in service_uuids]
        normalized_name = name.casefold()
        if normalized_name == RAW_DEVICE_NAME.casefold():
            firmware = "raw_stream"
        elif normalized_name == ONBOARD_DEVICE_NAME.casefold():
            firmware = "onboard_inference"
        else:
            firmware = "unknown"
        raw_compatible = firmware == "raw_stream"
        devices.append(
            {
                "address": device.address,
                "name": name,
                "rssi": rssi,
                "service_uuids": service_uuids,
                "firmware": firmware,
                "raw_compatible": raw_compatible,
                # Retained in scan responses for older dashboard clients.
                "edge_compatible": raw_compatible,
                "likely_xiao": firmware != "unknown"
                or RAW_SERVICE_UUID in normalized_services,
            }
        )
    devices.sort(key=lambda item: (not item["likely_xiao"], -(item["rssi"] or -999)))
    return devices
