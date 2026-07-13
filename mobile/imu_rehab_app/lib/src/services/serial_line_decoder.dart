import 'dart:convert';
import 'dart:typed_data';

class SerialLineDecoder {
  String _buffer = '';

  List<String> addBytes(Uint8List bytes) {
    final text = utf8.decode(bytes, allowMalformed: true);
    _buffer += text;

    final parts = _buffer.split('\n');
    _buffer = parts.removeLast();

    return parts
        .map((line) => line.replaceAll('\r', '').trim())
        .where((line) => line.isNotEmpty)
        .toList(growable: false);
  }

  String? flush() {
    final line = _buffer.replaceAll('\r', '').trim();
    _buffer = '';
    return line.isEmpty ? null : line;
  }
}
