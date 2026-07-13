import '../constants.dart';

class SensorWindow {
  const SensorWindow({
    required this.deviceId,
    required this.windowId,
    required this.sampleIntervalMs,
    required this.inputFrameSize,
    required this.collectStartUs,
    required this.collectEndUs,
    required this.features,
    required this.phoneReceiveUs,
  });

  final String deviceId;
  final int windowId;
  final double sampleIntervalMs;
  final int inputFrameSize;
  final int collectStartUs;
  final int collectEndUs;
  final List<double> features;
  final int phoneReceiveUs;

  factory SensorWindow.fromJson(
    Map<String, Object?> json, {
    required int phoneReceiveUs,
  }) {
    final featuresValue = json['features'];
    if (featuresValue is! List) {
      throw const FormatException('JSON window is missing a features array.');
    }

    final features = featuresValue
        .map((value) {
          if (value is! num) {
            throw const FormatException('Feature values must be numeric.');
          }
          return value.toDouble();
        })
        .toList(growable: false);

    if (features.length != ModelConstants.featureCount) {
      throw FormatException(
        'Expected ${ModelConstants.featureCount} features, got ${features.length}.',
      );
    }

    final inputFrameSize = _readInt(
      json,
      'input_frame_size',
      fallback: ModelConstants.featureCount,
    );
    if (inputFrameSize != ModelConstants.featureCount) {
      throw FormatException(
        'Expected input_frame_size ${ModelConstants.featureCount}, got $inputFrameSize.',
      );
    }

    return SensorWindow(
      deviceId: _readString(json, 'device_id', fallback: 'unknown'),
      windowId: _readInt(json, 'window_id'),
      sampleIntervalMs: _readDouble(
        json,
        'sample_interval_ms',
        fallback: ModelConstants.sampleIntervalMs,
      ),
      inputFrameSize: inputFrameSize,
      collectStartUs: _readInt(json, 't_collect_start_us', fallback: 0),
      collectEndUs: _readInt(json, 't_collect_end_us', fallback: 0),
      features: features,
      phoneReceiveUs: phoneReceiveUs,
    );
  }

  static String _readString(
    Map<String, Object?> json,
    String key, {
    required String fallback,
  }) {
    final value = json[key];
    if (value == null) {
      return fallback;
    }
    if (value is String) {
      return value;
    }
    throw FormatException('$key must be a string.');
  }

  static int _readInt(
    Map<String, Object?> json,
    String key, {
    int? fallback,
  }) {
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
    throw FormatException('$key must be an integer.');
  }

  static double _readDouble(
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
    throw FormatException('$key must be numeric.');
  }
}
