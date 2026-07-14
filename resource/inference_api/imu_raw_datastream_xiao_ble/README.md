# XIAO ESP32S3 continuous BLE IMU stream

`imu_raw_datastream_xiao_ble.ino` continuously samples an ICM-20948 and sends
one raw sample per BLE notification while a client is connected. It does not
run inference and does not wait for a start command.

## Hardware

| ICM-20948 | XIAO ESP32S3 |
| --- | --- |
| SDA | D4 / GPIO5 |
| SCL | D5 / GPIO6 |
| VIN / VCC | 3V3 |
| GND | GND |

The sketch assumes I2C address `0x68`. The default sample rate is 50 Hz. It can
be changed with `SAMPLE_RATE_HZ`; the accepted range is 1-200 Hz.

## BLE interface

The board advertises as `IMU-Raw-Stream` using Nordic UART Service UUIDs:

| Item | UUID |
| --- | --- |
| Service | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| TX notification | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` |

Subscribe to the TX characteristic. Every notification contains exactly one
20-byte, little-endian packet:

| Offset | Type | Field |
| ---: | --- | --- |
| 0 | `uint32` | Sequence number; restarts at zero on connection |
| 4 | `uint32` | Device `micros()` timestamp; wraps naturally |
| 8 | `int16` | Accelerometer X raw count |
| 10 | `int16` | Accelerometer Y raw count |
| 12 | `int16` | Accelerometer Z raw count |
| 14 | `int16` | Gyroscope X raw count |
| 16 | `int16` | Gyroscope Y raw count |
| 18 | `int16` | Gyroscope Z raw count |

The reset-default sensor ranges are +/-2 g and +/-250 degrees/second:

- acceleration in g = raw count / 16384
- angular velocity in degrees/second = raw count / 131

A future Python client can decode each Bleak notification with:

```python
import struct

sequence, time_us, ax, ay, az, gx, gy, gz = struct.unpack("<II6h", data)
accel_g = (ax / 16384.0, ay / 16384.0, az / 16384.0)
gyro_dps = (gx / 131.0, gy / 131.0, gz / 131.0)
```

Because the packet is exactly 20 bytes, it fits the default BLE ATT payload and
does not require application-level chunk reassembly.
