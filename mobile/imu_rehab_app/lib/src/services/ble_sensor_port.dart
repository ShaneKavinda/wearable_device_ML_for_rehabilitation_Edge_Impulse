import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import 'serial_line_decoder.dart';

class BleSensorDevice {
  const BleSensorDevice({
    required this.device,
    required this.name,
    required this.remoteId,
    required this.rssi,
    required this.isLikelyEsp32,
    required this.details,
  });

  final BluetoothDevice device;
  final String name;
  final String remoteId;
  final int rssi;
  final bool isLikelyEsp32;
  final String details;
}

class BleScanSnapshot {
  const BleScanSnapshot({
    required this.devices,
    required this.totalAdvertisements,
    required this.namedAdvertisements,
  });

  final List<BleSensorDevice> devices;
  final int totalAdvertisements;
  final int namedAdvertisements;
}

class BleSensorPort {
  BleSensorPort({SerialLineDecoder? decoder})
    : _decoder = decoder ?? SerialLineDecoder();

  static final Guid serviceUuid = Guid('6E400001-B5A3-F393-E0A9-E50E24DCCA9E');
  static final Guid rxUuid = Guid('6E400002-B5A3-F393-E0A9-E50E24DCCA9E');
  static final Guid txUuid = Guid('6E400003-B5A3-F393-E0A9-E50E24DCCA9E');
  static const advertisedName = 'IMU-Datastream';
  static const MethodChannel _permissionsChannel = MethodChannel(
    'imu_rehab/android_permissions',
  );

  final SerialLineDecoder _decoder;
  final StreamController<String> _linesController =
      StreamController<String>.broadcast();
  final StreamController<bool> _connectionController =
      StreamController<bool>.broadcast();

  BluetoothDevice? _device;
  BluetoothCharacteristic? _rxCharacteristic;
  StreamSubscription<List<int>>? _notifySubscription;
  StreamSubscription<BluetoothConnectionState>? _connectionSubscription;
  bool _connected = false;

  Stream<String> get lines => _linesController.stream;
  Stream<bool> get connectionChanges => _connectionController.stream;
  Stream<bool> get scanState => FlutterBluePlus.isScanning;
  bool get isConnected => _connected;

  Stream<BleScanSnapshot> get scanResults {
    return FlutterBluePlus.scanResults.map((results) {
      final devices = results
          .where((result) => result.advertisementData.connectable)
          .map((result) {
            final name = _displayName(result);
            return BleSensorDevice(
              device: result.device,
              name: name,
              remoteId: result.device.remoteId.toString(),
              rssi: result.rssi,
              isLikelyEsp32: _matchesSensor(result),
              details: _scanDetails(result),
            );
          })
          .toList(growable: false);

      devices.sort((a, b) {
        if (a.isLikelyEsp32 != b.isLikelyEsp32) {
          return a.isLikelyEsp32 ? -1 : 1;
        }
        return b.rssi.compareTo(a.rssi);
      });

      return BleScanSnapshot(
        devices: devices,
        totalAdvertisements: results.length,
        namedAdvertisements: results
            .where((result) => _displayName(result) != 'Unnamed BLE device')
            .length,
      );
    });
  }

  Future<void> startScan({
    Duration timeout = const Duration(seconds: 8),
  }) async {
    if (!await FlutterBluePlus.isSupported) {
      throw StateError('Bluetooth LE is not supported on this phone.');
    }

    await _ensureBluetoothScanPermissions();
    await _waitForAdapter();
    if (FlutterBluePlus.isScanningNow) {
      await FlutterBluePlus.stopScan();
    }

    await FlutterBluePlus.startScan(
      timeout: timeout,
      androidLegacy: true,
      androidScanMode: AndroidScanMode.lowLatency,
    );
  }

  static Future<void> _ensureBluetoothScanPermissions() async {
    try {
      final granted = await _permissionsChannel.invokeMethod<bool>(
        'ensureBluetoothScanPermissions',
      );
      if (granted != true) {
        throw StateError(
          'Bluetooth scan permission was denied. Allow Nearby Devices, then scan again.',
        );
      }
    } on MissingPluginException {
      return;
    }
  }

  Future<void> stopScan() async {
    if (FlutterBluePlus.isScanningNow) {
      await FlutterBluePlus.stopScan();
    }
  }

  Future<void> connect(BleSensorDevice sensor) async {
    await disconnect();
    await stopScan();
    await _waitForAdapter();

    final device = sensor.device;
    _device = device;

    _connectionSubscription = device.connectionState.listen((state) {
      final connected = state == BluetoothConnectionState.connected;
      if (_connected != connected) {
        _connected = connected;
        _connectionController.add(connected);
      }
      if (!connected) {
        _rxCharacteristic = null;
        unawaited(_notifySubscription?.cancel() ?? Future<void>.value());
        _notifySubscription = null;
      }
    });

    try {
      await device.connect(
        license: License.nonprofit,
        timeout: const Duration(seconds: 15),
      );
      await _requestMtuIfAvailable(device);

      final services = await device.discoverServices();
      final service = _findService(services, serviceUuid);
      if (service == null) {
        throw StateError('ESP32 inference BLE service was not found.');
      }

      final rx = _findCharacteristic(service, rxUuid);
      final tx = _findCharacteristic(service, txUuid);
      if (rx == null || tx == null) {
        throw StateError(
          'ESP32 inference BLE RX/TX characteristics were not found.',
        );
      }

      _rxCharacteristic = rx;
      _notifySubscription = tx.onValueReceived.listen((value) {
        final lines = _decoder.addBytes(Uint8List.fromList(value));
        for (final line in lines) {
          _linesController.add(line);
        }
      });
      await tx.setNotifyValue(true);

      _connected = true;
      _connectionController.add(true);
      await requestMenu();
    } on Object {
      await disconnect();
      rethrow;
    }
  }

  Future<void> requestMenu() {
    return writeCommand('m');
  }

  Future<void> selectGesture(int selection) {
    return writeCommand(selection.toString());
  }

  Future<void> stopSession() {
    return writeCommand('x');
  }

  Future<void> resetSensor() {
    return writeCommand('r');
  }

  Future<void> writeCommand(String command) async {
    final rx = _rxCharacteristic;
    if (!_connected || rx == null) {
      throw StateError('ESP32 BLE device is not connected.');
    }

    await rx.write(
      utf8.encode('$command\n'),
      withoutResponse: rx.properties.writeWithoutResponse,
    );
  }

  Future<void> disconnect() async {
    await _notifySubscription?.cancel();
    _notifySubscription = null;
    await _connectionSubscription?.cancel();
    _connectionSubscription = null;
    _rxCharacteristic = null;

    final device = _device;
    _device = null;
    if (device != null && _connected) {
      try {
        await device.disconnect();
      } on Object {
        // The OS may already have torn down the connection.
      }
    }

    if (_connected) {
      _connected = false;
      _connectionController.add(false);
    }
  }

  Future<void> dispose() async {
    await stopScan();
    await disconnect();
    await _linesController.close();
    await _connectionController.close();
  }

  static bool _matchesSensor(ScanResult result) {
    final advertisedName = result.advertisementData.advName;
    final platformName = result.device.platformName;
    if (_isLikelyEsp32Name(advertisedName) ||
        _isLikelyEsp32Name(platformName)) {
      return true;
    }
    return result.advertisementData.serviceUuids.any(
      (uuid) => _sameGuid(uuid, serviceUuid),
    );
  }

  static bool _isLikelyEsp32Name(String value) {
    final normalized = value.trim().toLowerCase();
    if (normalized.isEmpty) {
      return false;
    }
    return normalized == BleSensorPort.advertisedName.toLowerCase() ||
        normalized.contains('imu') ||
        normalized.contains('esp32') ||
        normalized.contains('xiao');
  }

  static String _displayName(ScanResult result) {
    final advertisedName = result.advertisementData.advName.trim();
    final platformName = result.device.platformName.trim();
    if (advertisedName.isNotEmpty) {
      return advertisedName;
    }
    if (platformName.isNotEmpty) {
      return platformName;
    }
    return 'Unnamed BLE device';
  }

  static String _scanDetails(ScanResult result) {
    final services = result.advertisementData.serviceUuids
        .map((uuid) => uuid.toString())
        .join(', ');
    final parts = <String>[
      result.advertisementData.connectable ? 'connectable' : 'not connectable',
      if (services.isNotEmpty) 'services: $services',
    ];
    return parts.join(' | ');
  }

  static bool _sameGuid(Object value, Guid guid) {
    return value.toString().toLowerCase() == guid.toString().toLowerCase();
  }

  static BluetoothService? _findService(
    List<BluetoothService> services,
    Guid uuid,
  ) {
    for (final service in services) {
      if (_sameGuid(service.uuid, uuid)) {
        return service;
      }
    }
    return null;
  }

  static BluetoothCharacteristic? _findCharacteristic(
    BluetoothService service,
    Guid uuid,
  ) {
    for (final characteristic in service.characteristics) {
      if (_sameGuid(characteristic.uuid, uuid)) {
        return characteristic;
      }
    }
    return null;
  }

  static Future<void> _waitForAdapter() async {
    if (FlutterBluePlus.adapterStateNow == BluetoothAdapterState.on) {
      return;
    }

    await FlutterBluePlus.adapterState
        .where((state) => state == BluetoothAdapterState.on)
        .first
        .timeout(
          const Duration(seconds: 12),
          onTimeout: () => throw TimeoutException(
            'Turn on Bluetooth and grant nearby-device permission.',
          ),
        );
  }

  static Future<void> _requestMtuIfAvailable(BluetoothDevice device) async {
    try {
      await device.requestMtu(185);
    } on Object {
      // Some platforms negotiate MTU automatically or do not expose this call.
    }
  }
}
