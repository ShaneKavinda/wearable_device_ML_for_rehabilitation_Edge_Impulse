import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:imu_rehab_app/src/constants.dart';
import 'package:imu_rehab_app/src/models/inference_result.dart';
import 'package:imu_rehab_app/src/models/sensor_window.dart';
import 'package:imu_rehab_app/src/services/benchmark_logger.dart';

void main() {
  test('writes CSV header and one benchmark row', () async {
    final directory = await Directory.systemTemp.createTemp('imu_rehab_log_');
    addTearDown(() => directory.delete(recursive: true));

    final logger = BenchmarkLogger(
      directory: directory,
      sessionId: 'session_test',
    );

    final file = await logger.append(
      BenchmarkLogEntry(
        sessionId: logger.sessionId,
        isoTimeUtc: DateTime.utc(2026, 6, 17, 8),
        window: _window(),
        result: _result(),
      ),
    );

    final lines = await file.readAsLines();
    expect(lines, hasLength(2));
    expect(lines.first, BenchmarkLogger.header.join(','));
    expect(lines.last, contains('session_test'));
    expect(lines.last, contains('Flexion'));
    expect(lines.last, contains('0.91'));
  });
}

SensorWindow _window() {
  return SensorWindow(
    deviceId: 'pico_w_001',
    windowId: 42,
    sampleIntervalMs: ModelConstants.sampleIntervalMs,
    inputFrameSize: ModelConstants.featureCount,
    collectStartUs: 100,
    collectEndUs: 200,
    phoneReceiveUs: 1000,
    features: List<double>.filled(ModelConstants.featureCount, 0),
  );
}

InferenceResult _result() {
  return InferenceResult(
    engineId: 'fake',
    windowId: 42,
    predictedLabel: 'Flexion',
    confidence: 0.91,
    scores: <String, double>{
      for (final label in ModelConstants.labels) label: label == 'Flexion' ? 0.91 : 0.02,
    },
    inferStartUs: 1200,
    inferEndUs: 1600,
    inferenceMs: 0.4,
  );
}
