import 'dart:convert';

import '../models/sensor_window.dart';
import 'clock.dart';

class SensorWindowParser {
  const SensorWindowParser();

  SensorWindow? tryParseLine(String line, {int? phoneReceiveUs}) {
    final trimmed = line.trim();
    if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
      return null;
    }

    final decoded = jsonDecode(trimmed);
    if (decoded is! Map) {
      throw const FormatException('Top-level JSON window must be an object.');
    }

    final type = decoded['type'];
    if (type is String && type != 'imu_window' && type != 'sensor_window') {
      return null;
    }
    if (decoded['ok'] == false) {
      return null;
    }

    return SensorWindow.fromJson(
      decoded.map<String, Object?>(
        (key, value) => MapEntry(key.toString(), value),
      ),
      phoneReceiveUs: phoneReceiveUs ?? nowMicros(),
    );
  }
}
