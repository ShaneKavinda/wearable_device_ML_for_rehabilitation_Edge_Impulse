import '../models/inference_result.dart';
import '../models/sensor_window.dart';

abstract class InferenceEngine {
  const InferenceEngine();

  String get engineId;
  bool get isAvailable => true;
  String? get unavailableReason => null;

  Future<void> warmUp();
  Future<InferenceResult> classify(SensorWindow window);
  Future<void> close();
}
