import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:imu_rehab_app/src/models/pc_api_models.dart';

void main() {
  test('parses state envelope with gesture menu and latest result', () {
    final envelope = PcApiEnvelope.fromText(
      jsonEncode(<String, Object?>{
        'type': 'state',
        'received_at': '2026-06-25T12:00:00+00:00',
        'data': _stateJson(),
      }),
    );
    final state = PcHubState.fromJson(envelope.data);

    expect(envelope.type, 'state');
    expect(state.connected, isTrue);
    expect(state.gestureMenu?.options, hasLength(2));
    expect(state.gestureMenu?.options.first.label, 'Flexion');
    expect(state.latestResult?.predicted, 'Flexion');
    expect(state.stats.validCount, 3);
  });

  test('parses session and repetition event envelopes', () {
    final sessionStart = PcApiEnvelope.fromText(
      jsonEncode(<String, Object?>{
        'type': 'session_start',
        'received_at': '2026-06-25T12:00:00+00:00',
        'data': <String, Object?>{
          'type': 'session_start',
          'target': 'Flexion',
          'repetitions': 10,
        },
      }),
    );
    final repetition = PcRepetitionEvent.fromJson(
      PcApiEnvelope.fromText(
        jsonEncode(<String, Object?>{
          'type': 'repetition_event',
          'received_at': '2026-06-25T12:00:01+00:00',
          'data': <String, Object?>{
            'type': 'repetition_event',
            'target': 'Flexion',
            'repetition': 1,
            'event': 'movement_start',
          },
        }),
      ).data,
    );

    expect(sessionStart.type, 'session_start');
    expect(sessionStart.data['target'], 'Flexion');
    expect(repetition.repetition, 1);
    expect(repetition.event, 'movement_start');
  });

  test('parses inference result scores timing and memory metrics', () {
    final result = PcInferenceResult.fromJson(_resultJson());

    expect(result.windowId, 1);
    expect(result.predicted, 'Flexion');
    expect(result.confidence, 0.91);
    expect(result.correct, isTrue);
    expect(result.trusted, isTrue);
    expect(result.scores['Flexion'], 0.91);
    expect(result.timingMs['classification'], 5);
    expect(result.memoryBytes['free_after'], 12000);
  });

  test('parses session summary and metrics envelopes', () {
    final summary = PcSessionSummary.fromJson(<String, Object?>{
      'type': 'session_summary',
      'target': 'Flexion',
      'total_repetitions': 10,
      'correct_count': 9,
      'accuracy': 0.9,
      'pass_count': 8,
      'pass_rate': 0.8,
      'uncertain_count': 1,
      'avg_pass_confidence': 0.92,
      'avg_inference_ms': 12.3,
      'min_free_memory_bytes': 11000,
    });
    final metrics = PcMetrics.fromJson(<String, Object?>{
      'valid_count': 12,
      'invalid_count': 1,
      'connected': true,
      'session_running': false,
      'log_file': 'logs/session.jsonl',
    });

    expect(summary.passCount, 8);
    expect(summary.minFreeMemoryBytes, 11000);
    expect(metrics.validCount, 12);
    expect(metrics.logFile, 'logs/session.jsonl');
  });
}

Map<String, Object?> _stateJson() {
  return <String, Object?>{
    'config': <String, Object?>{
      'source_type': 'edge_serial_beetle',
      'port': 'COM13',
      'baud': 115200,
      'save_invalid': false,
      'log_dir': 'logs',
    },
    'connected': true,
    'session_running': false,
    'gesture_menu': <String, Object?>{
      'type': 'gesture_menu',
      'device_id': 'beetle_rp2530_001',
      'gestures': <Object?>[
        <String, Object?>{'selection': 1, 'label': 'Flexion'},
        <String, Object?>{'selection': 2, 'label': 'Extension'},
      ],
    },
    'latest_result': _resultJson(),
    'latest_summary': null,
    'stats': <String, Object?>{'valid_count': 3, 'invalid_count': 0},
    'log_file': 'logs/session.jsonl',
    'last_error': null,
  };
}

Map<String, Object?> _resultJson() {
  return <String, Object?>{
    'type': 'inference_result',
    'device_id': 'beetle_rp2530_001',
    'repetition': 1,
    'window_id': 1,
    'target': 'Flexion',
    'ok': true,
    'label': 'Flexion',
    'predicted': 'Flexion',
    'correct': true,
    'trusted': true,
    'confidence': 0.91,
    'inference_ms': 12.3,
    'scores': <String, Object?>{'Flexion': 0.91, 'Extension': 0.02},
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
  };
}
