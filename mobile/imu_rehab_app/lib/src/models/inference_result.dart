class InferenceResult {
  const InferenceResult({
    required this.engineId,
    required this.windowId,
    required this.predictedLabel,
    required this.confidence,
    required this.scores,
    required this.inferStartUs,
    required this.inferEndUs,
    required this.inferenceMs,
    this.timing = const <String, double>{},
    this.error,
  });

  final String engineId;
  final int windowId;
  final String predictedLabel;
  final double confidence;
  final Map<String, double> scores;
  final Map<String, double> timing;
  final int inferStartUs;
  final int inferEndUs;
  final double inferenceMs;
  final String? error;

  bool get hasError {
    final message = error;
    return message != null && message.isNotEmpty;
  }

  factory InferenceResult.error({
    required String engineId,
    required int windowId,
    required String message,
    required int inferStartUs,
    required int inferEndUs,
  }) {
    return InferenceResult(
      engineId: engineId,
      windowId: windowId,
      predictedLabel: 'ERR',
      confidence: 0,
      scores: const <String, double>{},
      inferStartUs: inferStartUs,
      inferEndUs: inferEndUs,
      inferenceMs: (inferEndUs - inferStartUs) / 1000,
      error: message,
    );
  }
}
