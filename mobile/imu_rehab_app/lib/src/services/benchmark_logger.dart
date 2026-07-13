import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../constants.dart';
import '../models/inference_result.dart';
import '../models/sensor_window.dart';

class BenchmarkLogEntry {
  const BenchmarkLogEntry({
    required this.sessionId,
    required this.isoTimeUtc,
    required this.window,
    required this.result,
  });

  final String sessionId;
  final DateTime isoTimeUtc;
  final SensorWindow window;
  final InferenceResult result;

  List<Object?> toColumns() {
    final endToEndMs = (result.inferEndUs - window.phoneReceiveUs) / 1000;
    return <Object?>[
      sessionId,
      isoTimeUtc.toUtc().toIso8601String(),
      result.engineId,
      window.deviceId,
      window.windowId,
      window.features.length,
      window.collectStartUs,
      window.collectEndUs,
      window.phoneReceiveUs,
      result.inferStartUs,
      result.inferEndUs,
      result.inferenceMs,
      endToEndMs,
      result.predictedLabel,
      result.confidence,
      result.scores[ModelConstants.labels[0]] ?? 0,
      result.scores[ModelConstants.labels[1]] ?? 0,
      result.scores[ModelConstants.labels[2]] ?? 0,
      result.scores[ModelConstants.labels[3]] ?? 0,
      result.scores[ModelConstants.labels[4]] ?? 0,
      result.scores[ModelConstants.labels[5]] ?? 0,
      result.error ?? '',
    ];
  }
}

class BenchmarkLogger {
  BenchmarkLogger({required Directory directory, String? sessionId})
    : sessionId = sessionId ?? _buildLogSessionId(),
      _directory = directory;

  final Directory _directory;
  final String sessionId;
  File? _file;

  static const header = <String>[
    'session_id',
    'iso_time_utc',
    'engine_id',
    'device_id',
    'window_id',
    'feature_count',
    'collect_start_us',
    'collect_end_us',
    'phone_receive_us',
    'infer_start_us',
    'infer_end_us',
    'inference_ms',
    'end_to_end_ms',
    'predicted_label',
    'confidence',
    'score_extension',
    'score_flexion',
    'score_pronation',
    'score_radial_deviation',
    'score_supination',
    'score_ulnar_deviation',
    'error',
  ];

  File? get file => _file;

  static Future<BenchmarkLogger> createDefault() async {
    final baseDirectory = await getApplicationDocumentsDirectory();
    final logDirectory = Directory('${baseDirectory.path}/imu_rehab_logs');
    return BenchmarkLogger(directory: logDirectory);
  }

  Future<File> ensureFile() async {
    if (!await _directory.exists()) {
      await _directory.create(recursive: true);
    }

    final file = _file ?? File('${_directory.path}/$sessionId.csv');
    _file = file;

    if (!await file.exists()) {
      await file.writeAsString('${_csvLine(header)}\n');
    }
    return file;
  }

  Future<File> append(BenchmarkLogEntry entry) async {
    final file = await ensureFile();
    await file.writeAsString(
      '${_csvLine(entry.toColumns())}\n',
      mode: FileMode.append,
      flush: true,
    );
    return file;
  }

  static String _csvLine(Iterable<Object?> values) {
    return values.map(_escapeCsv).join(',');
  }

  static String _escapeCsv(Object? value) {
    final text = value?.toString() ?? '';
    final mustQuote =
        text.contains(',') || text.contains('"') || text.contains('\n');
    final escaped = text.replaceAll('"', '""');
    return mustQuote ? '"$escaped"' : escaped;
  }
}

class OnboardMetricsLogEntry {
  const OnboardMetricsLogEntry._({
    required this.sessionId,
    required this.isoTimeUtc,
    required this.eventType,
    required this.values,
  });

  factory OnboardMetricsLogEntry.inferenceResult({
    required String sessionId,
    required DateTime isoTimeUtc,
    required Map<String, Object?> json,
  }) {
    final timing = _readMap(json, 'timing_ms');
    final memory = _readMap(json, 'memory_bytes');
    final scores = _readMap(json, 'scores');

    return OnboardMetricsLogEntry._(
      sessionId: sessionId,
      isoTimeUtc: isoTimeUtc,
      eventType: 'inference_result',
      values: <String, Object?>{
        'device_id': json['device_id'],
        'target': json['target'],
        'repetition': json['repetition'],
        'window_id': json['window_id'],
        'ok': json['ok'],
        'predicted_label': json['predicted'] ?? json['label'],
        'correct': json['correct'],
        'trusted': json['trusted'],
        'accuracy': json['accuracy'],
        'accuracy_percent': json['accuracy_percent'],
        'confidence': json['confidence'],
        'confidence_threshold': json['confidence_threshold'],
        'collect_ms': json['collect_ms'],
        'inference_ms': json['inference_ms'],
        'timing_wall_ms': timing['wall'],
        'timing_dsp_ms': timing['dsp'],
        'timing_classification_ms': timing['classification'],
        'timing_anomaly_ms': timing['anomaly'],
        'free_memory_before_bytes': memory['free_before'],
        'free_memory_after_bytes': memory['free_after'],
        'free_memory_delta_bytes': memory['free_delta'],
        'sample_count': json['sample_count'],
        'sample_interval_ms': json['sample_interval_ms'],
        for (final label in ModelConstants.labels)
          'score_${_csvKey(label)}': scores[label],
        'error': json['error'],
      },
    );
  }

  factory OnboardMetricsLogEntry.sessionSummary({
    required String sessionId,
    required DateTime isoTimeUtc,
    required Map<String, Object?> json,
  }) {
    return OnboardMetricsLogEntry._(
      sessionId: sessionId,
      isoTimeUtc: isoTimeUtc,
      eventType: 'session_summary',
      values: <String, Object?>{
        'device_id': json['device_id'],
        'target': json['target'],
        'accuracy': json['accuracy'],
        'accuracy_percent': json['accuracy_percent'],
        'pass_rate': json['pass_rate'],
        'pass_rate_percent': json['pass_rate_percent'],
        'avg_pass_confidence': json['avg_pass_confidence'],
        'avg_inference_ms': json['avg_inference_ms'],
        'min_free_memory_bytes': json['min_free_memory_bytes'],
        'summary_total_repetitions': json['total_repetitions'],
        'summary_correct_count': json['correct_count'],
        'summary_pass_count': json['pass_count'],
        'summary_uncertain_count': json['uncertain_count'],
      },
    );
  }

  final String sessionId;
  final DateTime isoTimeUtc;
  final String eventType;
  final Map<String, Object?> values;

  List<Object?> toColumns() {
    return <Object?>[
      sessionId,
      isoTimeUtc.toUtc().toIso8601String(),
      eventType,
      for (final column in OnboardMetricsLogger.metricColumns) values[column],
    ];
  }
}

class OnboardMetricsLogger {
  OnboardMetricsLogger({required Directory directory, String? sessionId})
    : sessionId = sessionId ?? _buildLogSessionId(),
      _directory = directory;

  final Directory _directory;
  final String sessionId;
  File? _file;

  static const metricColumns = <String>[
    'device_id',
    'target',
    'repetition',
    'window_id',
    'ok',
    'predicted_label',
    'correct',
    'trusted',
    'accuracy',
    'accuracy_percent',
    'pass_rate',
    'pass_rate_percent',
    'confidence',
    'confidence_threshold',
    'collect_ms',
    'inference_ms',
    'timing_wall_ms',
    'timing_dsp_ms',
    'timing_classification_ms',
    'timing_anomaly_ms',
    'free_memory_before_bytes',
    'free_memory_after_bytes',
    'free_memory_delta_bytes',
    'min_free_memory_bytes',
    'sample_count',
    'sample_interval_ms',
    'score_extension',
    'score_flexion',
    'score_pronation',
    'score_radial_deviation',
    'score_supination',
    'score_ulnar_deviation',
    'avg_pass_confidence',
    'avg_inference_ms',
    'summary_total_repetitions',
    'summary_correct_count',
    'summary_pass_count',
    'summary_uncertain_count',
    'error',
  ];

  static const header = <String>[
    'session_id',
    'iso_time_utc',
    'event_type',
    ...metricColumns,
  ];

  File? get file => _file;

  static Future<OnboardMetricsLogger> createDefault() async {
    final baseDirectory = await getApplicationDocumentsDirectory();
    final logDirectory = Directory('${baseDirectory.path}/imu_rehab_logs');
    return OnboardMetricsLogger(directory: logDirectory);
  }

  Future<File> ensureFile() async {
    if (!await _directory.exists()) {
      await _directory.create(recursive: true);
    }

    final file = _file ?? File('${_directory.path}/onboard_$sessionId.csv');
    _file = file;

    if (!await file.exists()) {
      await file.writeAsString('${BenchmarkLogger._csvLine(header)}\n');
    }
    return file;
  }

  Future<File> append(OnboardMetricsLogEntry entry) async {
    final file = await ensureFile();
    await file.writeAsString(
      '${BenchmarkLogger._csvLine(entry.toColumns())}\n',
      mode: FileMode.append,
      flush: true,
    );
    return file;
  }
}

String _buildLogSessionId() {
  final now = DateTime.now().toUtc();
  final safeIso = now.toIso8601String().replaceAll(RegExp(r'[:.]'), '-');
  return 'session_$safeIso';
}

Map<String, Object?> _readMap(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is Map) {
    return value.map<String, Object?>(
      (key, value) => MapEntry(key.toString(), value),
    );
  }
  return const <String, Object?>{};
}

String _csvKey(String label) {
  return label.toLowerCase().replaceAll(' ', '_');
}
