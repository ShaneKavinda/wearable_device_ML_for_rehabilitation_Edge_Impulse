import 'package:flutter_test/flutter_test.dart';
import 'package:imu_rehab_app/src/constants.dart';
import 'package:imu_rehab_app/src/models/inference_result.dart';
import 'package:imu_rehab_app/src/models/sensor_window.dart';
import 'package:imu_rehab_app/src/services/inference_engine.dart';

void main() {
  test('fake inference engine follows the shared inference contract', () async {
    final engine = FakeEngine();
    final result = await engine.classify(_window());

    expect(engine.engineId, 'fake');
    expect(result.predictedLabel, 'Extension');
    expect(result.confidence, 0.75);
    expect(result.scores, containsPair('Extension', 0.75));
  });
}

class FakeEngine extends InferenceEngine {
  @override
  String get engineId => 'fake';

  @override
  Future<void> close() async {}

  @override
  Future<void> warmUp() async {}

  @override
  Future<InferenceResult> classify(SensorWindow window) async {
    return InferenceResult(
      engineId: engineId,
      windowId: window.windowId,
      predictedLabel: 'Extension',
      confidence: 0.75,
      scores: <String, double>{
        for (final label in ModelConstants.labels) label: label == 'Extension' ? 0.75 : 0.05,
      },
      inferStartUs: 100,
      inferEndUs: 300,
      inferenceMs: 0.2,
    );
  }
}

SensorWindow _window() {
  return SensorWindow(
    deviceId: 'pico_w_001',
    windowId: 1,
    sampleIntervalMs: ModelConstants.sampleIntervalMs,
    inputFrameSize: ModelConstants.featureCount,
    collectStartUs: 10,
    collectEndUs: 20,
    phoneReceiveUs: 30,
    features: List<double>.filled(ModelConstants.featureCount, 0),
  );
}
