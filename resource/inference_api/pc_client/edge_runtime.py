"""Compatibility imports for code written before the REST architecture split.

New code should import IMU acquisition from ``imu_source``, deployment adapters
from ``model_backends``, and packet definitions from ``inference_protocol``.
"""

try:
    from .imu_source import (  # noqa: F401
        CAPTURE_TIMEOUT_SECONDS,
        GESTURE_MENU,
        ONBOARD_DEVICE_NAME,
        RAW_DEVICE_NAME,
        RAW_PACKET,
        RAW_SERVICE_UUID,
        RAW_TX_UUID,
        SAMPLE_COUNT,
        CapturedWindow,
        ImuSourceError,
        RawImuPacket,
        SamplingContractError,
        SequenceGapError,
        XiaoBleImuSource,
        ble_runtime_status,
        scan_ble_devices,
    )
    from .inference_protocol import (  # noqa: F401
        FEATURE_COUNT,
        MODEL_LABELS,
        RESULT_PACKET,
        InferenceProtocolError,
        ResultPacketData,
        pack_result_packet,
        unpack_result_packet,
    )
    from .model_backends import (  # noqa: F401
        RUNNER_FEATURES,
        RUNNER_REQUEST_HEADER,
        RUNNER_REQUEST_MAGIC,
        RUNNER_RESPONSE,
        RUNNER_RESPONSE_MAGIC,
        EdgeInferenceRunner,
        InferenceOutput,
        LocalModelBackend,
        ModelBackendError,
        RestModelBackend,
        create_model_backend,
    )
except ImportError:
    from imu_source import (  # noqa: F401
        CAPTURE_TIMEOUT_SECONDS,
        GESTURE_MENU,
        ONBOARD_DEVICE_NAME,
        RAW_DEVICE_NAME,
        RAW_PACKET,
        RAW_SERVICE_UUID,
        RAW_TX_UUID,
        SAMPLE_COUNT,
        CapturedWindow,
        ImuSourceError,
        RawImuPacket,
        SamplingContractError,
        SequenceGapError,
        XiaoBleImuSource,
        ble_runtime_status,
        scan_ble_devices,
    )
    from inference_protocol import (  # noqa: F401
        FEATURE_COUNT,
        MODEL_LABELS,
        RESULT_PACKET,
        InferenceProtocolError,
        ResultPacketData,
        pack_result_packet,
        unpack_result_packet,
    )
    from model_backends import (  # noqa: F401
        RUNNER_FEATURES,
        RUNNER_REQUEST_HEADER,
        RUNNER_REQUEST_MAGIC,
        RUNNER_RESPONSE,
        RUNNER_RESPONSE_MAGIC,
        EdgeInferenceRunner,
        InferenceOutput,
        LocalModelBackend,
        ModelBackendError,
        RestModelBackend,
        create_model_backend,
    )

# Transitional names for external scripts. The source no longer owns a runner.
XiaoBleEdgeSource = XiaoBleImuSource
EdgeRuntimeError = RuntimeError
