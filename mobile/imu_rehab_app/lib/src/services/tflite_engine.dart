import '../models/inference_result.dart';
import '../models/sensor_window.dart';
import 'clock.dart';
import 'inference_engine.dart';

class TfliteEngine extends InferenceEngine {
  const TfliteEngine();

  @override
  String get engineId => 'tflite_asset';

  @override
  bool get isAvailable => false;

  @override
  String get unavailableReason =>
      'Add assets/model/gesture_model.tflite and validate it against the Edge Impulse native engine.';

  @override
  Future<void> warmUp() async {}

  @override
  Future<InferenceResult> classify(SensorWindow window) async {
    final start = nowMicros();
    final end = nowMicros();
    return InferenceResult.error(
      engineId: engineId,
      windowId: window.windowId,
      message: unavailableReason,
      inferStartUs: start,
      inferEndUs: end,
    );
  }

  @override
  Future<void> close() async {}
}
