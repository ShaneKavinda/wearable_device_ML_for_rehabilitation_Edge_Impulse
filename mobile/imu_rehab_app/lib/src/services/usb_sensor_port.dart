import 'dart:async';
import 'dart:typed_data';

import 'package:usb_serial/usb_serial.dart';

import 'serial_line_decoder.dart';

class UsbSensorPort {
  UsbSensorPort({SerialLineDecoder? decoder})
      : _decoder = decoder ?? SerialLineDecoder();

  final SerialLineDecoder _decoder;
  final StreamController<String> _linesController =
      StreamController<String>.broadcast();

  UsbPort? _port;
  StreamSubscription<Uint8List>? _subscription;

  Stream<String> get lines => _linesController.stream;
  bool get isConnected => _port != null;

  Future<List<UsbDevice>> listDevices() {
    return UsbSerial.listDevices();
  }

  Future<void> connect(UsbDevice device) async {
    await disconnect();

    final port = await device.create();
    if (port == null) {
      throw StateError('Could not create a USB serial port.');
    }

    final opened = await port.open();
    if (!opened) {
      throw StateError('Could not open the USB serial port.');
    }

    await port.setDTR(true);
    await port.setRTS(true);
    await port.setPortParameters(
      115200,
      UsbPort.DATABITS_8,
      UsbPort.STOPBITS_1,
      UsbPort.PARITY_NONE,
    );

    _subscription = port.inputStream?.listen((chunk) {
      for (final line in _decoder.addBytes(chunk)) {
        _linesController.add(line);
      }
    });
    _port = port;
  }

  Future<void> requestWindow() async {
    final port = _port;
    if (port == null) {
      throw StateError('USB serial port is not connected.');
    }
    await port.write(Uint8List.fromList('s\n'.codeUnits));
  }

  Future<void> disconnect() async {
    await _subscription?.cancel();
    _subscription = null;
    await _port?.close();
    _port = null;
  }

  Future<void> dispose() async {
    await disconnect();
    await _linesController.close();
  }
}
