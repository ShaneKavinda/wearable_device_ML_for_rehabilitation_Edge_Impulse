import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:imu_rehab_app/src/constants.dart';
import 'package:imu_rehab_app/src/services/sensor_window_parser.dart';

void main() {
  const parser = SensorWindowParser();

  test('ignores non-json serial chatter', () {
    expect(parser.tryParseLine('System Ready.'), isNull);
    expect(parser.tryParseLine('>> Select a Gesture <<'), isNull);
  });

  test('ignores non-window json status messages', () {
    expect(
      parser.tryParseLine(
        '{"type":"status","device_id":"xiao","message":"ready"}',
        phoneReceiveUs: 1234,
      ),
      isNull,
    );
  });

  test('parses a valid feature window', () {
    final window = parser.tryParseLine(_windowJson(), phoneReceiveUs: 1234);

    expect(window, isNotNull);
    final parsedWindow = window;
    if (parsedWindow == null) {
      fail('Expected a parsed sensor window.');
    }
    expect(parsedWindow.deviceId, 'pico_w_001');
    expect(parsedWindow.windowId, 7);
    expect(parsedWindow.inputFrameSize, ModelConstants.featureCount);
    expect(parsedWindow.features, hasLength(ModelConstants.featureCount));
    expect(parsedWindow.phoneReceiveUs, 1234);
  });

  test('rejects incorrect feature count', () {
    final line = _windowJson(features: <double>[1, 2, 3]);

    expect(
      () => parser.tryParseLine(line, phoneReceiveUs: 1234),
      throwsFormatException,
    );
  });
}

String _windowJson({List<double>? features}) {
  return jsonEncode(<String, Object?>{
    'device_id': 'pico_w_001',
    'window_id': 7,
    'sample_interval_ms': ModelConstants.sampleIntervalMs,
    'input_frame_size': ModelConstants.featureCount,
    't_collect_start_us': 100,
    't_collect_end_us': 200,
    'features':
        features ??
        List<double>.generate(
          ModelConstants.featureCount,
          (index) => index / 10,
        ),
  });
}
