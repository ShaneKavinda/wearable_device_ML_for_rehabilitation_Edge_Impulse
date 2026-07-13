import 'package:flutter/services.dart';

import '../constants.dart';
import '../models/inference_result.dart';
import '../models/sensor_window.dart';
import 'clock.dart';
import 'inference_engine.dart';

class EdgeImpulseNativeEngine extends InferenceEngine {
  EdgeImpulseNativeEngine({
    MethodChannel channel = const MethodChannel('imu_rehab/edge_impulse'),
  }) : _channel = channel;

  final MethodChannel _channel;

  @override
  String get engineId => 'edge_impulse_native';

  @override
  Future<void> warmUp() async {
    await _channel.invokeMethod<void>('warmUp');
  }

  @override
  Future<InferenceResult> classify(SensorWindow window) async {
    final inferStartUs = nowMicros();
    try {
      final result = await _channel.invokeMapMethod<String, Object?>(
        'classify',
        <String, Object?>{
          'windowId': window.windowId,
          'features': window.features,
        },
      );
      final inferEndUs = nowMicros();

      if (result == null) {
        return InferenceResult.error(
          engineId: engineId,
          windowId: window.windowId,
          message: 'Native inference returned no result.',
          inferStartUs: inferStartUs,
          inferEndUs: inferEndUs,
        );
      }

      final error = result['error'] as String?;
      if (error != null && error.isNotEmpty) {
        return InferenceResult.error(
          engineId: engineId,
          windowId: window.windowId,
          message: error,
          inferStartUs: inferStartUs,
          inferEndUs: inferEndUs,
        );
      }

      final scores = _readScoreMap(result['scores']);
      final timing = _readScoreMap(result['timing']);
      final label = (result['predictedLabel'] as String?) ?? _topLabel(scores);
      final confidence =
          (result['confidence'] as num?)?.toDouble() ?? (scores[label] ?? 0);

      return InferenceResult(
        engineId: engineId,
        windowId: window.windowId,
        predictedLabel: label,
        confidence: confidence,
        scores: scores,
        timing: timing,
        inferStartUs: inferStartUs,
        inferEndUs: inferEndUs,
        inferenceMs: (inferEndUs - inferStartUs) / 1000,
      );
    } on PlatformException catch (error) {
      final inferEndUs = nowMicros();
      return InferenceResult.error(
        engineId: engineId,
        windowId: window.windowId,
        message: error.message ?? error.code,
        inferStartUs: inferStartUs,
        inferEndUs: inferEndUs,
      );
    } on Object catch (error) {
      final inferEndUs = nowMicros();
      return InferenceResult.error(
        engineId: engineId,
        windowId: window.windowId,
        message: error.toString(),
        inferStartUs: inferStartUs,
        inferEndUs: inferEndUs,
      );
    }
  }

  @override
  Future<void> close() async {
    await _channel.invokeMethod<void>('close');
  }

  static Map<String, double> _readScoreMap(Object? value) {
    if (value is! Map) {
      return const <String, double>{};
    }
    return value.map<String, double>(
      (key, score) => MapEntry(
        key.toString(),
        score is num ? score.toDouble() : 0,
      ),
    );
  }

  static String _topLabel(Map<String, double> scores) {
    if (scores.isEmpty) {
      return 'unknown';
    }
    return scores.entries.reduce((a, b) => a.value >= b.value ? a : b).key;
  }
}

Map<String, double> emptyScores() {
  return <String, double>{
    for (final label in ModelConstants.labels) label: 0,
  };
}
