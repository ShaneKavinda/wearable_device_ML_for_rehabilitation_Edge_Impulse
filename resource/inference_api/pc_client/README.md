# IMU Rehab PC API Hub

The PC client is the hub between inference sources and remote clients. It owns
source communication, exposes REST and WebSocket APIs, logs JSONL records, and
serves a browser setup GUI.

## Install

```powershell
cd resource\inference_api\pc_client
py -m pip install -r requirements.txt
```

## Run The API Hub

```powershell
py datastream_client.py serve --host 0.0.0.0 --port 8765
```

Open the setup GUI on the PC:

```text
http://127.0.0.1:8765
```

From a phone on the same network, use the PC's LAN address:

```text
http://<pc-ip>:8765
```

## Web GUI Scope

The browser GUI is only for PC-side setup and diagnostics:

* choose the source type;
* set serial port, baud, and log options;
* connect or disconnect the active source;
* inspect source health, record counts, and log path.

Gesture/session operation is intentionally handled by the mobile app through the
API.

## Setup Flow

1. Close Arduino Serial Monitor, Serial Plotter, PuTTY, Thonny, or anything else
   using the COM port.
2. Start the API hub.
3. Open the web GUI.
4. Set source to `edge_serial_beetle`, enter the COM port and baud, then connect.
5. Open the Flutter app and connect it to `http://<pc-ip>:8765`.
6. Start gesture sessions from the mobile app.

The current source registry is future-ready, but only `edge_serial_beetle` is
implemented.

## API

REST:

```text
GET  /api/health
GET  /api/sources
GET  /api/serial/ports
GET  /api/state
PUT  /api/config
POST /api/source/connect
POST /api/source/disconnect
POST /api/source/request-menu
POST /api/session/start
POST /api/session/stop
GET  /api/logs
```

WebSocket:

```text
GET /ws/results
```

The WebSocket emits mobile-safe envelopes with these types:

```text
state
status
session_start
repetition_event
inference_result
session_summary
metrics
error
```

Raw serial lines and feature windows are not sent to mobile clients.

## Run One Hardware Smoke Test

```powershell
py datastream_client.py run-once --port COM13 --baud 115200 --gesture Flexion
```

## Test

```powershell
py -m unittest discover -s . -p test_*.py
```
