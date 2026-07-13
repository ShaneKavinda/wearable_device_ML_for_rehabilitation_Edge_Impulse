import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:imu_rehab_app/src/services/pc_api_client.dart';

void main() {
  test('normalizes API URL and fills missing scheme', () {
    final uri = PcApiClient.normalizeBaseUriText('192.168.1.20:8765');

    expect(uri.scheme, 'http');
    expect(uri.host, '192.168.1.20');
    expect(uri.port, 8765);
  });

  test('rejects unsupported API URL schemes', () {
    expect(
      () => PcApiClient.normalizeBaseUri(Uri.parse('ftp://example.com')),
      throwsFormatException,
    );
  });

  test('decodes string and byte websocket envelopes', () async {
    final controller = StreamController<dynamic>();
    addTearDown(controller.close);

    final envelopes = decodeEnvelopeStream(controller.stream).take(2).toList();
    controller.add(
      jsonEncode(<String, Object?>{
        'type': 'metrics',
        'received_at': '2026-06-25T12:00:00+00:00',
        'data': <String, Object?>{'valid_count': 1, 'invalid_count': 0},
      }),
    );
    controller.add(
      utf8.encode(
        jsonEncode(<String, Object?>{
          'type': 'session_summary',
          'received_at': '2026-06-25T12:00:01+00:00',
          'data': <String, Object?>{'target': 'Flexion'},
        }),
      ),
    );

    final decoded = await envelopes;

    expect(decoded.first.type, 'metrics');
    expect(decoded.last.type, 'session_summary');
    expect(decoded.last.data['target'], 'Flexion');
  });
}
