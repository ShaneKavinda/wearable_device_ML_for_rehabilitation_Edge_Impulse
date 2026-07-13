import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:imu_rehab_app/src/services/serial_line_decoder.dart';

void main() {
  test('assembles newline terminated serial chunks', () {
    final decoder = SerialLineDecoder();

    expect(decoder.addBytes(Uint8List.fromList('abc'.codeUnits)), isEmpty);
    expect(
      decoder.addBytes(Uint8List.fromList('123\nnext'.codeUnits)),
      <String>['abc123'],
    );
    expect(decoder.addBytes(Uint8List.fromList('\r\n'.codeUnits)), <String>[
      'next',
    ]);
  });

  test('flush returns trailing buffered line', () {
    final decoder = SerialLineDecoder();

    decoder.addBytes(Uint8List.fromList('partial'.codeUnits));

    expect(decoder.flush(), 'partial');
    expect(decoder.flush(), isNull);
  });
}
