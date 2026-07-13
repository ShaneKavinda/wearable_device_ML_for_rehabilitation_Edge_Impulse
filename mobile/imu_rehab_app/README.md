# IMU Rehab Flutter App

Android-first Flutter app for connecting directly to the ESP32 over BLE and
displaying inference sessions generated on the ESP32.

## Bluetooth Sensor Requirement

Flash the BLE Arduino sketch first:

```text
resource/inference_api/imu_datastream_ble/imu_datastream_ble.ino
```

The app scans for a peripheral advertising the Nordic UART Service used by that
sketch:

| Direction | UUID |
| --- | --- |
| Service | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX write | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` |
| TX notify | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` |

The ESP32 is the inference device. The phone does not look for the raw IMU
sensor and does not run the model. It connects to the ESP32, receives the menu,
sends the selected gesture number, and displays newline-delimited JSON
notifications from the ESP32.

## Mobile Flow

1. Turn on Bluetooth on the Android phone.
2. Launch the app and grant nearby-device permissions when prompted.
3. Tap `Scan`, choose the ESP32 peripheral advertising `IMU-Datastream`, then
   tap `Connect`.
4. The ESP32 sends a `gesture_menu`; choose the desired gesture task.
5. Tap `Start Session` to send the selected number from `1` to `6`.
6. Follow the countdown and movement prompts while the ESP32 samples the IMU,
   runs inference locally, and sends results back to the app.
7. Use `Stop Session`, `Reset IMU`, and `Menu` for direct ESP32 control.

The app displays the latest ESP32 prediction, confidence, class scores,
repetition status, result counts, IMU status, collection timing, inference
timing, and session summary.

## BLE Payloads

The dashboard currently handles:

| Type | Purpose |
| --- | --- |
| `gesture_menu` | Populates the gesture task selector |
| `session_start` | Marks the task session as running |
| `repetition_event` | Shows countdown, movement, and next-repetition state |
| `inference_result` | Displays one onboard classifier result |
| `session_summary` | Displays completed-session accuracy/pass metrics |
| `status` | Displays ESP32 startup, reset, stop, and error messages |

## Run

```powershell
cd mobile\imu_rehab_app
flutter pub get
flutter test
flutter build apk --debug
```

## Notes

The older USB serial, phone-side inference, and PC API services remain in the
codebase for reference, but the default dashboard now uses ESP32 BLE inference
directly.
