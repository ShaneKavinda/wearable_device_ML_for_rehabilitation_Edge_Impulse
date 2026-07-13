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

  test('writes onboard metrics rows for results and summaries', () async {
    final directory = await Directory.systemTemp.createTemp(
      'imu_rehab_onboard_log_',
    );
    addTearDown(() => directory.delete(recursive: true));

    final logger = OnboardMetricsLogger(
      directory: directory,
      sessionId: 'session_onboard_test',
    );

    await logger.append(
      OnboardMetricsLogEntry.inferenceResult(
        sessionId: logger.sessionId,
        isoTimeUtc: DateTime.utc(2026, 7, 13, 8),
        json: _onboardResultJson(),
      ),
    );
    final file = await logger.append(
      OnboardMetricsLogEntry.sessionSummary(
        sessionId: logger.sessionId,
        isoTimeUtc: DateTime.utc(2026, 7, 13, 8, 1),
        json: _onboardSummaryJson(),
      ),
    );

    final lines = await file.readAsLines();
    expect(lines, hasLength(3));
    expect(lines.first, OnboardMetricsLogger.header.join(','));
    expect(lines[1], contains('inference_result'));
    expect(lines[1], contains('Flexion'));
    expect(lines[1], contains('12000'));
    expect(lines[1], contains('12.3'));
    expect(lines[2], contains('session_summary'));
    expect(lines[2], contains('0.9'));
    expect(lines[2], contains('11000'));
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
      for (final label in ModelConstants.labels)
        label: label == 'Flexion' ? 0.91 : 0.02,
    },
    inferStartUs: 1200,
    inferEndUs: 1600,
    inferenceMs: 0.4,
  );
}

Map<String, Object?> _onboardResultJson() {
  return <String, Object?>{
    'type': 'inference_result',
    'device_id': 'xiao_esp32s3_ble_001',
    'repetition': 1,
    'window_id': 1,
    'target': 'Flexion',
    'ok': true,
    'predicted': 'Flexion',
    'correct': true,
    'trusted': true,
    'accuracy': 1.0,
    'accuracy_percent': 100,
    'confidence': 0.91,
    'confidence_threshold': 0.85,
    'collect_ms': 2000.0,
    'inference_ms': 12.3,
    'timing_ms': <String, Object?>{
      'wall': 12.3,
      'dsp': 7,
      'classification': 5,
      'anomaly': 0,
    },
    'memory_bytes': <String, Object?>{
      'free_before': 12300,
      'free_after': 12000,
      'free_delta': -300,
    },
    'sample_count': 200,
    'sample_interval_ms': 10.0,
    'scores': <String, Object?>{
      for (final label in ModelConstants.labels)
        label: label == 'Flexion' ? 0.91 : 0.02,
    },
    'error': null,
  };
}

Map<String, Object?> _onboardSummaryJson() {
  return <String, Object?>{
    'type': 'session_summary',
    'device_id': 'xiao_esp32s3_ble_001',
    'target': 'Flexion',
    'total_repetitions': 10,
    'correct_count': 9,
    'accuracy': 0.9,
    'accuracy_percent': 90.0,
    'pass_count': 8,
    'pass_rate': 0.8,
    'pass_rate_percent': 80.0,
    'uncertain_count': 1,
    'avg_pass_confidence': 0.92,
    'avg_inference_ms': 12.3,
    'min_free_memory_bytes': 11000,
  };
}
