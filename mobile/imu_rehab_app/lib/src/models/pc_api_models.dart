import 'dart:convert';

class GestureOption {
  const GestureOption({required this.selection, required this.label});

  final int selection;
  final String label;

  factory GestureOption.fromJson(Map<String, Object?> json) {
    return GestureOption(
      selection: _readInt(json, 'selection'),
      label: _readString(json, 'label', fallback: ''),
    );
  }
}

class GestureMenu {
  const GestureMenu({required this.deviceId, required this.options});

  final String deviceId;
  final List<GestureOption> options;

  factory GestureMenu.fromJson(Map<String, Object?> json) {
    final gestures = json['gestures'];
    return GestureMenu(
      deviceId: _readString(json, 'device_id', fallback: 'unknown'),
      options: gestures is List
          ? gestures
                .whereType<Map>()
                .map(
                  (item) => GestureOption.fromJson(
                    item.map<String, Object?>(
                      (key, value) => MapEntry(key.toString(), value),
                    ),
                  ),
                )
                .toList(growable: false)
          : const <GestureOption>[],
    );
  }
}

class PcApiConfig {
  const PcApiConfig({
    required this.sourceType,
    required this.baud,
    required this.saveInvalid,
    required this.logDir,
    this.port,
  });

  final String sourceType;
  final String? port;
  final int baud;
  final bool saveInvalid;
  final String logDir;

  factory PcApiConfig.fromJson(Map<String, Object?> json) {
    return PcApiConfig(
      sourceType: _readString(
        json,
        'source_type',
        fallback: 'edge_serial_beetle',
      ),
      port: _readNullableString(json, 'port'),
      baud: _readInt(json, 'baud', fallback: 115200),
      saveInvalid: _readBool(json, 'save_invalid', fallback: false),
      logDir: _readString(json, 'log_dir', fallback: 'logs'),
    );
  }
}

class PcApiStats {
  const PcApiStats({required this.validCount, required this.invalidCount});

  final int validCount;
  final int invalidCount;

  factory PcApiStats.fromJson(Map<String, Object?> json) {
    return PcApiStats(
      validCount: _readInt(json, 'valid_count', fallback: 0),
      invalidCount: _readInt(json, 'invalid_count', fallback: 0),
    );
  }
}

class PcHubState {
  const PcHubState({
    required this.config,
    required this.connected,
    required this.sessionRunning,
    required this.stats,
    this.gestureMenu,
    this.latestResult,
    this.latestSummary,
    this.logFile,
    this.lastError,
  });

  final PcApiConfig config;
  final bool connected;
  final bool sessionRunning;
  final GestureMenu? gestureMenu;
  final PcInferenceResult? latestResult;
  final PcSessionSummary? latestSummary;
  final PcApiStats stats;
  final String? logFile;
  final String? lastError;

  factory PcHubState.fromJson(Map<String, Object?> json) {
    return PcHubState(
      config: PcApiConfig.fromJson(_readMap(json, 'config')),
      connected: _readBool(json, 'connected', fallback: false),
      sessionRunning: _readBool(json, 'session_running', fallback: false),
      gestureMenu: _nullableMap(json, 'gesture_menu') == null
          ? null
          : GestureMenu.fromJson(_readMap(json, 'gesture_menu')),
      latestResult: _nullableMap(json, 'latest_result') == null
          ? null
          : PcInferenceResult.fromJson(_readMap(json, 'latest_result')),
      latestSummary: _nullableMap(json, 'latest_summary') == null
          ? null
          : PcSessionSummary.fromJson(_readMap(json, 'latest_summary')),
      stats: PcApiStats.fromJson(_readMap(json, 'stats')),
      logFile: _readNullableString(json, 'log_file'),
      lastError: _readNullableString(json, 'last_error'),
    );
  }
}

class PcApiEnvelope {
  const PcApiEnvelope({
    required this.type,
    required this.receivedAt,
    required this.data,
  });

  final String type;
  final DateTime? receivedAt;
  final Map<String, Object?> data;

  factory PcApiEnvelope.fromJson(Map<String, Object?> json) {
    return PcApiEnvelope(
      type: _readString(json, 'type', fallback: 'unknown'),
      receivedAt: DateTime.tryParse(
        _readString(json, 'received_at', fallback: ''),
      ),
      data: _readMap(json, 'data'),
    );
  }

  factory PcApiEnvelope.fromText(String text) {
    final decoded = jsonDecode(text);
    if (decoded is! Map) {
      throw const FormatException('API envelope must be a JSON object.');
    }
    return PcApiEnvelope.fromJson(
      decoded.map<String, Object?>(
        (key, value) => MapEntry(key.toString(), value),
      ),
    );
  }
}

class PcInferenceResult {
  const PcInferenceResult({
    required this.repetition,
    required this.windowId,
    required this.target,
    required this.predicted,
    required this.correct,
    required this.trusted,
    required this.confidence,
    required this.scores,
    required this.timingMs,
    required this.memoryBytes,
    required this.inferenceMs,
    required this.ok,
    this.error,
  });

  final int repetition;
  final int windowId;
  final String target;
  final String predicted;
  final bool correct;
  final bool trusted;
  final double confidence;
  final Map<String, double> scores;
  final Map<String, double> timingMs;
  final Map<String, int?> memoryBytes;
  final double inferenceMs;
  final bool ok;
  final String? error;

  factory PcInferenceResult.fromJson(Map<String, Object?> json) {
    return PcInferenceResult(
      repetition: _readInt(json, 'repetition', fallback: 0),
      windowId: _readInt(json, 'window_id', fallback: 0),
      target: _readString(json, 'target', fallback: ''),
      predicted: _readString(
        json,
        'predicted',
        fallback: _readString(json, 'label', fallback: 'Waiting'),
      ),
      correct: _readBool(json, 'correct', fallback: false),
      trusted: _readBool(json, 'trusted', fallback: false),
      confidence: _readDouble(json, 'confidence', fallback: 0),
      scores: _readDoubleMap(json, 'scores'),
      timingMs: _readDoubleMap(json, 'timing_ms'),
      memoryBytes: _readNullableIntMap(json, 'memory_bytes'),
      inferenceMs: _readDouble(json, 'inference_ms', fallback: 0),
      ok: _readBool(json, 'ok', fallback: true),
      error: _readNullableString(json, 'error'),
    );
  }
}

class PcSessionSummary {
  const PcSessionSummary({
    required this.target,
    required this.totalRepetitions,
    required this.correctCount,
    required this.accuracy,
    required this.passCount,
    required this.passRate,
    required this.uncertainCount,
    required this.avgPassConfidence,
    required this.avgInferenceMs,
    this.minFreeMemoryBytes,
  });

  final String target;
  final int totalRepetitions;
  final int correctCount;
  final double accuracy;
  final int passCount;
  final double passRate;
  final int uncertainCount;
  final double avgPassConfidence;
  final double avgInferenceMs;
  final int? minFreeMemoryBytes;

  factory PcSessionSummary.fromJson(Map<String, Object?> json) {
    return PcSessionSummary(
      target: _readString(json, 'target', fallback: ''),
      totalRepetitions: _readInt(json, 'total_repetitions', fallback: 0),
      correctCount: _readInt(json, 'correct_count', fallback: 0),
      accuracy: _readDouble(json, 'accuracy', fallback: 0),
      passCount: _readInt(json, 'pass_count', fallback: 0),
      passRate: _readDouble(json, 'pass_rate', fallback: 0),
      uncertainCount: _readInt(json, 'uncertain_count', fallback: 0),
      avgPassConfidence: _readDouble(json, 'avg_pass_confidence', fallback: 0),
      avgInferenceMs: _readDouble(json, 'avg_inference_ms', fallback: 0),
      minFreeMemoryBytes: _readNullableInt(json, 'min_free_memory_bytes'),
    );
  }
}

class PcMetrics {
  const PcMetrics({
    required this.validCount,
    required this.invalidCount,
    required this.connected,
    required this.sessionRunning,
    this.logFile,
  });

  final int validCount;
  final int invalidCount;
  final bool connected;
  final bool sessionRunning;
  final String? logFile;

  factory PcMetrics.fromJson(Map<String, Object?> json) {
    return PcMetrics(
      validCount: _readInt(json, 'valid_count', fallback: 0),
      invalidCount: _readInt(json, 'invalid_count', fallback: 0),
      connected: _readBool(json, 'connected', fallback: false),
      sessionRunning: _readBool(json, 'session_running', fallback: false),
      logFile: _readNullableString(json, 'log_file'),
    );
  }
}

class PcRepetitionEvent {
  const PcRepetitionEvent({
    required this.repetition,
    required this.event,
    required this.target,
  });

  final int repetition;
  final String event;
  final String target;

  factory PcRepetitionEvent.fromJson(Map<String, Object?> json) {
    return PcRepetitionEvent(
      repetition: _readInt(json, 'repetition', fallback: 0),
      event: _readString(json, 'event', fallback: ''),
      target: _readString(json, 'target', fallback: ''),
    );
  }
}

Map<String, Object?> _readMap(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is Map) {
    return value.map<String, Object?>(
      (key, value) => MapEntry(key.toString(), value),
    );
  }
  return <String, Object?>{};
}

Map<String, Object?>? _nullableMap(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) {
    return null;
  }
  return _readMap(json, key);
}

String _readString(
  Map<String, Object?> json,
  String key, {
  required String fallback,
}) {
  final value = json[key];
  if (value == null) {
    return fallback;
  }
  return value.toString();
}

String? _readNullableString(Map<String, Object?> json, String key) {
  final value = json[key];
  return value?.toString();
}

bool _readBool(
  Map<String, Object?> json,
  String key, {
  required bool fallback,
}) {
  final value = json[key];
  if (value is bool) {
    return value;
  }
  if (value == null) {
    return fallback;
  }
  return value.toString().toLowerCase() == 'true';
}

int _readInt(Map<String, Object?> json, String key, {int? fallback}) {
  final value = json[key];
  if (value == null && fallback != null) {
    return fallback;
  }
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  if (value is String) {
    return int.parse(value);
  }
  throw FormatException('$key must be an integer.');
}

int? _readNullableInt(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value == null) {
    return null;
  }
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  if (value is String) {
    return int.tryParse(value);
  }
  return null;
}

double _readDouble(
  Map<String, Object?> json,
  String key, {
  required double fallback,
}) {
  final value = json[key];
  if (value == null) {
    return fallback;
  }
  if (value is num) {
    return value.toDouble();
  }
  if (value is String) {
    return double.parse(value);
  }
  throw FormatException('$key must be numeric.');
}

Map<String, double> _readDoubleMap(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! Map) {
    return const <String, double>{};
  }
  return value.map<String, double>((key, value) {
    final number = value is num ? value.toDouble() : double.parse('$value');
    return MapEntry(key.toString(), number);
  });
}

Map<String, int?> _readNullableIntMap(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! Map) {
    return const <String, int?>{};
  }
  return value.map<String, int?>((key, value) {
    if (value == null) {
      return MapEntry(key.toString(), null);
    }
    if (value is int) {
      return MapEntry(key.toString(), value);
    }
    if (value is num) {
      return MapEntry(key.toString(), value.toInt());
    }
    return MapEntry(key.toString(), int.tryParse('$value'));
  });
}
