# Mobile App Log Fields

The mobile app writes CSV logs under the app documents directory in
`imu_rehab_logs`. The dashboard share button exports the active onboard metrics
CSV, named like `onboard_session_<timestamp>.csv`.

## Onboard Metrics Log

This is the active log written by the BLE dashboard. It records data received
from the ESP32/XIAO onboard inference firmware. Rows can represent either a
single classifier result (`event_type = inference_result`) or a completed
session summary (`event_type = session_summary`). Fields that do not apply to a
row type are left empty.

| Field | Applies to | Meaning |
| --- | --- | --- |
| `session_id` | all rows | Unique mobile logging session ID generated when the app logger starts. |
| `iso_time_utc` | all rows | UTC timestamp when the phone wrote the log row. |
| `event_type` | all rows | Row type: `inference_result` or `session_summary`. |
| `device_id` | all rows | ID reported by the ESP32/XIAO firmware. |
| `target` | all rows | Gesture selected for the session, such as `Flexion`. |
| `repetition` | result rows | Repetition number within the current session. |
| `window_id` | result rows | Firmware-generated ID for the sampled IMU window. |
| `ok` | result rows | Whether onboard inference completed successfully. |
| `predicted_label` | result rows | Gesture label predicted by the onboard model. |
| `correct` | result rows | Whether `predicted_label` matches `target`. |
| `trusted` | result rows | Whether confidence is at or above the firmware confidence threshold. |
| `accuracy` | all rows | Result rows: `1.0` for correct or `0.0` for incorrect. Summary rows: session accuracy as a fraction. |
| `accuracy_percent` | all rows | Accuracy as a percentage. |
| `pass_rate` | summary rows | Fraction of repetitions that passed the firmware success criteria. |
| `pass_rate_percent` | summary rows | Pass rate as a percentage. |
| `confidence` | result rows | Model confidence for the predicted label, from `0.0` to `1.0`. |
| `confidence_threshold` | result rows | Firmware threshold used to mark a prediction as trusted. |
| `collect_ms` | result rows | Time the firmware spent collecting the IMU sample window, in milliseconds. |
| `inference_ms` | result rows | Total measured time around `run_classifier(...)` on the board, in milliseconds. |
| `timing_wall_ms` | result rows | Edge Impulse wall-clock inference time. In the current firmware this mirrors `inference_ms`. |
| `timing_dsp_ms` | result rows | Edge Impulse DSP/preprocessing time, in milliseconds. |
| `timing_classification_ms` | result rows | Edge Impulse classifier/neural-network time, in milliseconds. |
| `timing_anomaly_ms` | result rows | Edge Impulse anomaly detection time, in milliseconds. Usually `0` if anomaly detection is not enabled. |
| `free_memory_before_bytes` | result rows | Free heap memory before onboard inference starts, in bytes. |
| `free_memory_after_bytes` | result rows | Free heap memory after onboard inference finishes, in bytes. |
| `free_memory_delta_bytes` | result rows | Change in free memory: `after - before`, in bytes. |
| `min_free_memory_bytes` | summary rows | Lowest free-memory value observed across successful session results, in bytes. |
| `sample_count` | result rows | Number of IMU samples collected for the inference window. |
| `sample_interval_ms` | result rows | Target interval between IMU samples, in milliseconds. |
| `score_extension` | result rows | Model score for `Extension`. |
| `score_flexion` | result rows | Model score for `Flexion`. |
| `score_pronation` | result rows | Model score for `Pronation`. |
| `score_radial_deviation` | result rows | Model score for `Radial Deviation`. |
| `score_supination` | result rows | Model score for `Supination`. |
| `score_ulnar_deviation` | result rows | Model score for `Ulnar Deviation`. |
| `avg_pass_confidence` | summary rows | Average confidence across passed repetitions. |
| `avg_inference_ms` | summary rows | Average onboard inference time across successful results, in milliseconds. |
| `summary_total_repetitions` | summary rows | Total repetitions expected in the session. |
| `summary_correct_count` | summary rows | Number of repetitions predicted correctly. |
| `summary_pass_count` | summary rows | Number of repetitions that passed the firmware success criteria. |
| `summary_uncertain_count` | summary rows | Number of repetitions below the confidence threshold. |
| `error` | result rows | Firmware error message when inference fails; empty for successful results. |

## Legacy Phone-Side Benchmark Log

The codebase also contains an older logger for phone-side inference benchmarks.
It is not the CSV shared by the current BLE dashboard, but these fields may
appear if that path is used again.

| Field | Meaning |
| --- | --- |
| `session_id` | Unique mobile logging session ID. |
| `iso_time_utc` | UTC timestamp for the log row. |
| `engine_id` | Phone-side inference engine ID, such as `fake` or a native engine name. |
| `device_id` | Sensor device ID from the parsed sensor window. |
| `window_id` | Sensor window ID. |
| `feature_count` | Number of numeric features in the window. |
| `collect_start_us` | Device timestamp when sensor collection started, in microseconds. |
| `collect_end_us` | Device timestamp when sensor collection ended, in microseconds. |
| `phone_receive_us` | Phone timestamp when the sensor window was received, in microseconds. |
| `infer_start_us` | Phone timestamp when inference started, in microseconds. |
| `infer_end_us` | Phone timestamp when inference ended, in microseconds. |
| `inference_ms` | Phone-side inference duration, in milliseconds. |
| `end_to_end_ms` | Time from phone receive to inference end, in milliseconds. |
| `predicted_label` | Gesture label predicted by the phone-side engine. |
| `confidence` | Confidence for the predicted label, from `0.0` to `1.0`. |
| `score_extension` | Model score for `Extension`. |
| `score_flexion` | Model score for `Flexion`. |
| `score_pronation` | Model score for `Pronation`. |
| `score_radial_deviation` | Model score for `Radial Deviation`. |
| `score_supination` | Model score for `Supination`. |
| `score_ulnar_deviation` | Model score for `Ulnar Deviation`. |
| `error` | Inference error message, if any. |
