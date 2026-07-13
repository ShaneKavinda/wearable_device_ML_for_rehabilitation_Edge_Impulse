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
  BenchmarkLogger({
    required Directory directory,
    String? sessionId,
  })  : sessionId = sessionId ?? _buildSessionId(),
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

  static String _buildSessionId() {
    final now = DateTime.now().toUtc();
    final safeIso = now.toIso8601String().replaceAll(RegExp(r'[:.]'), '-');
    return 'session_$safeIso';
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
