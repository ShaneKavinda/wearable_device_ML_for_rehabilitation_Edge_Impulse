from __future__ import annotations

import struct
from dataclasses import dataclass


MODEL_PROJECT_ID = 738400
MODEL_DEPLOY_VERSION = 19
MODEL_FREQUENCY_HZ = 16.5
SAMPLE_COUNT = 33
AXES_PER_SAMPLE = 6
FEATURE_COUNT = SAMPLE_COUNT * AXES_PER_SAMPLE
SAMPLE_INTERVAL_MS = 1000.0 / MODEL_FREQUENCY_HZ
SAMPLE_INTERVAL_US = 1_000_000.0 / MODEL_FREQUENCY_HZ
EXPECTED_DEVICE_SPAN_MS = (SAMPLE_COUNT - 1) * SAMPLE_INTERVAL_MS
FEATURE_AXES = (
    "acc_x",
    "acc_y",
    "acc_z",
    "gyro_x",
    "gyro_y",
    "gyro_z",
)
FEATURE_UNITS = ("g", "g", "g", "deg/s", "deg/s", "deg/s")
ACCEL_LSB_PER_G = 16384.0
GYRO_LSB_PER_DPS = 131.0

assert FEATURE_COUNT == 198
MODEL_LABELS = (
    "Extension",
    "Flexion",
    "Pronation",
    "Radial Deviation",
    "Supination",
    "Ulnar Deviation",
)
RESULT_PACKET = struct.Struct("<BBHIIIHBB")


class InferenceProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResultPacketData:
    version: int
    deployment: int
    flags: int
    window_id: int
    source_sequence: int
    inference_us: int
    confidence_q15: int
    repetition: int
    predicted_class: int

    @property
    def confidence(self) -> float:
        return min(self.confidence_q15, 32767) / 32767.0

    @property
    def ok(self) -> bool:
        return bool(self.flags & 0x0001)

    @property
    def trusted(self) -> bool:
        return bool(self.flags & 0x0002)

    @property
    def correct(self) -> bool:
        return bool(self.flags & 0x0004)


def pack_result_packet(
    *,
    deployment: int,
    window_id: int,
    source_sequence: int,
    inference_us: int,
    confidence: float,
    repetition: int,
    predicted_class: int,
    ok: bool,
    trusted: bool,
    correct: bool,
) -> bytes:
    flags = (
        (0x0001 if ok else 0)
        | (0x0002 if trusted else 0)
        | (0x0004 if correct else 0)
    )
    confidence_q15 = round(max(0.0, min(1.0, confidence)) * 32767.0)
    return RESULT_PACKET.pack(
        1,
        deployment,
        flags,
        window_id & 0xFFFFFFFF,
        source_sequence & 0xFFFFFFFF,
        inference_us & 0xFFFFFFFF,
        confidence_q15,
        repetition & 0xFF,
        predicted_class & 0xFF,
    )


def unpack_result_packet(data: bytes) -> ResultPacketData:
    if len(data) != RESULT_PACKET.size:
        raise InferenceProtocolError(
            f"Inference result must be {RESULT_PACKET.size} bytes, got {len(data)}."
        )
    result = ResultPacketData(*RESULT_PACKET.unpack(data))
    if result.version != 1:
        raise InferenceProtocolError(
            f"Unsupported result packet version: {result.version}"
        )
    return result
