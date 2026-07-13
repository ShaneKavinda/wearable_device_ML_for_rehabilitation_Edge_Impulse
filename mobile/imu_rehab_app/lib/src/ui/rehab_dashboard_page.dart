import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';

import '../constants.dart';
import '../models/pc_api_models.dart';
import '../services/benchmark_logger.dart';
import '../services/ble_sensor_port.dart';

class RehabDashboardPage extends StatefulWidget {
  const RehabDashboardPage({super.key});

  @override
  State<RehabDashboardPage> createState() => _RehabDashboardPageState();
}

class _RehabDashboardPageState extends State<RehabDashboardPage> {
  final BleSensorPort _esp32Port = BleSensorPort();

  StreamSubscription<BleScanSnapshot>? _scanResultsSubscription;
  StreamSubscription<bool>? _scanStateSubscription;
  StreamSubscription<bool>? _connectionSubscription;
  StreamSubscription<String>? _lineSubscription;
  late final Future<OnboardMetricsLogger> _metricsLoggerFuture;

  List<BleSensorDevice> _devices = const <BleSensorDevice>[];
  final List<_DirectEvent> _events = <_DirectEvent>[];
  GestureMenu? _gestureMenu;
  PcInferenceResult? _latestResult;
  PcSessionSummary? _latestSummary;
  PcRepetitionEvent? _latestRepetitionEvent;
  Map<String, Object?> _latestRawResult = const <String, Object?>{};
  String? _selectedDeviceId;
  String? _selectedGesture;
  String _status = 'ESP32 disconnected';
  String _boardResponse = 'Waiting for XIAO board';
  String _activeTarget = '';
  File? _metricsLogFile;
  bool _scanning = false;
  bool _connected = false;
  bool _busy = false;
  bool _sessionRunning = false;
  bool _takingReadings = false;
  bool _sharingLog = false;
  int _scanAdvertisementCount = 0;
  int _scanNamedCount = 0;
  int _resultCount = 0;
  int _statusCount = 0;
  int _invalidLineCount = 0;
  int _loggedMetricCount = 0;

  @override
  void initState() {
    super.initState();
    _metricsLoggerFuture = OnboardMetricsLogger.createDefault();
    unawaited(_prepareMetricsLog());
    _scanResultsSubscription = _esp32Port.scanResults.listen(
      _handleScanResults,
    );
    _scanStateSubscription = _esp32Port.scanState.listen((scanning) {
      if (!mounted) {
        return;
      }
      setState(() {
        _scanning = scanning;
        if (!scanning && !_connected && _status.startsWith('Scanning')) {
          _status = _devices.isEmpty
              ? 'No ESP32 candidates found ($_scanAdvertisementCount BLE seen)'
              : 'Select the ESP32 candidate';
        }
      });
    });
    _connectionSubscription = _esp32Port.connectionChanges.listen((connected) {
      if (!mounted) {
        return;
      }
      setState(() {
        _connected = connected;
        if (!connected) {
          _sessionRunning = false;
          _takingReadings = false;
          _boardResponse = 'XIAO disconnected';
          _status = 'ESP32 disconnected';
          _addEvent('ESP32 disconnected');
        }
      });
    });
    _lineSubscription = _esp32Port.lines.listen(_handleEsp32Line);
  }

  @override
  void dispose() {
    _scanResultsSubscription?.cancel();
    _scanStateSubscription?.cancel();
    _connectionSubscription?.cancel();
    _lineSubscription?.cancel();
    unawaited(_esp32Port.dispose());
    super.dispose();
  }

  Future<void> _scanForEsp32() async {
    if (_busy || _connected) {
      return;
    }

    setState(() {
      _busy = true;
      _status = 'Scanning for ESP32';
      _devices = const <BleSensorDevice>[];
      _selectedDeviceId = null;
      _scanAdvertisementCount = 0;
      _scanNamedCount = 0;
      _addEvent('scan started');
    });

    try {
      await _esp32Port.startScan();
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = false;
        _status = 'Scanning for ESP32 candidates';
      });
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = false;
        _status = 'Scan failed: $error';
        _addEvent('scan failed');
      });
    }
  }

  Future<void> _stopScan() async {
    await _esp32Port.stopScan();
  }

  Future<void> _connectSelectedEsp32() async {
    final esp32 = _selectedEsp32;
    if (esp32 == null || _busy) {
      return;
    }

    setState(() {
      _busy = true;
      _status = 'Connecting to ${esp32.name}';
      _addEvent('connecting to ${esp32.name}');
    });

    try {
      await _esp32Port.connect(esp32);
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = false;
        _connected = true;
        _status = 'Connected to ${esp32.name}; waiting for menu';
        _addEvent('ESP32 connected');
      });
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = false;
        _connected = false;
        _status = 'Connect failed: $error';
        _addEvent('connect failed');
      });
    }
  }

  Future<void> _disconnectEsp32() async {
    if (_busy) {
      return;
    }

    setState(() {
      _busy = true;
      _status = 'Disconnecting ESP32';
    });

    await _esp32Port.disconnect();
    if (!mounted) {
      return;
    }
    setState(() {
      _busy = false;
      _connected = false;
      _sessionRunning = false;
      _takingReadings = false;
      _boardResponse = 'XIAO disconnected';
      _status = 'ESP32 disconnected';
      _addEvent('ESP32 disconnected');
    });
  }

  Future<void> _requestMenu() async {
    await _sendEsp32Command('Requesting gesture menu', _esp32Port.requestMenu);
  }

  Future<void> _startSession() async {
    final selection = int.tryParse(_selectedGesture ?? '');
    if (selection == null) {
      setState(() => _status = 'Select a gesture first');
      return;
    }
    await _sendEsp32Command(
      'Starting gesture session',
      () => _esp32Port.selectGesture(selection),
    );
  }

  Future<void> _stopSession() async {
    await _sendEsp32Command('Stopping session', _esp32Port.stopSession);
  }

  Future<void> _resetEsp32Imu() async {
    await _sendEsp32Command('Resetting ESP32 IMU', _esp32Port.resetSensor);
  }

  Future<void> _sendEsp32Command(
    String message,
    Future<void> Function() command,
  ) async {
    if (!_connected || _busy) {
      return;
    }

    setState(() {
      _busy = true;
      _status = message;
    });

    try {
      await command();
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = false;
        _status = '$message sent';
        _addEvent(message.toLowerCase());
      });
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = false;
        _status = 'Command failed: $error';
        _addEvent('command failed');
      });
    }
  }

  void _handleScanResults(BleScanSnapshot snapshot) {
    if (!mounted) {
      return;
    }

    setState(() {
      _scanAdvertisementCount = snapshot.totalAdvertisements;
      _scanNamedCount = snapshot.namedAdvertisements;
      _devices = snapshot.devices;
      if (snapshot.devices.isNotEmpty &&
          (_selectedDeviceId == null ||
              !snapshot.devices.any(
                (device) => device.remoteId == _selectedDeviceId,
              ))) {
        _selectedDeviceId = snapshot.devices.first.remoteId;
      }
    });
  }

  void _handleEsp32Line(String line) {
    try {
      final trimmed = line.trim();
      if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
        return;
      }

      final decoded = jsonDecode(trimmed);
      if (decoded is! Map) {
        throw const FormatException('ESP32 message must be a JSON object.');
      }

      final json = decoded.map<String, Object?>(
        (key, value) => MapEntry(key.toString(), value),
      );
      final type = (json['type'] ?? '').toString();

      switch (type) {
        case 'gesture_menu':
          _handleGestureMenu(json);
          break;
        case 'session_start':
          _handleSessionStart(json);
          break;
        case 'repetition_event':
          _handleRepetitionEvent(json);
          break;
        case 'inference_result':
          _handleInferenceResult(json);
          break;
        case 'session_summary':
          _handleSessionSummary(json);
          break;
        case 'status':
          _handleStatus(json);
          break;
      }
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _invalidLineCount += 1;
        _status = 'ESP32 parse failed: $error';
        _addEvent('parse failed');
      });
    }
  }

  void _handleGestureMenu(Map<String, Object?> json) {
    final menu = GestureMenu.fromJson(json);
    setState(() {
      _gestureMenu = menu;
      final options = menu.options;
      if (options.isNotEmpty &&
          (_selectedGesture == null ||
              !options.any(
                (option) => option.selection.toString() == _selectedGesture,
              ))) {
        _selectedGesture = options.first.selection.toString();
      }
      _status = 'Gesture menu received';
      _addEvent('gesture menu received');
    });
  }

  void _handleSessionStart(Map<String, Object?> json) {
    setState(() {
      _sessionRunning = true;
      _latestSummary = null;
      _latestRepetitionEvent = null;
      _activeTarget = (json['target'] ?? '').toString();
      _takingReadings = false;
      _boardResponse = _activeTarget.isEmpty
          ? 'Session started'
          : 'Session started\n$_activeTarget';
      _status = 'Session started: $_activeTarget';
      _addEvent(_status);
    });
  }

  void _handleRepetitionEvent(Map<String, Object?> json) {
    final event = PcRepetitionEvent.fromJson(json);
    setState(() {
      _latestRepetitionEvent = event;
      _takingReadings = event.event == 'movement_start';
      if (event.event == 'sampling_finished') {
        _takingReadings = false;
      }
      _boardResponse = _boardResponseForEvent(event);
      _status = 'Rep ${event.repetition}: ${_labelForEvent(event.event)}';
      _addEvent(_status);
    });
  }

  void _handleInferenceResult(Map<String, Object?> json) {
    final result = PcInferenceResult.fromJson(json);
    unawaited(
      _appendMetricsLog(
        (logger, timestamp) => OnboardMetricsLogEntry.inferenceResult(
          sessionId: logger.sessionId,
          isoTimeUtc: timestamp,
          json: json,
        ),
      ),
    );
    setState(() {
      _latestResult = result;
      _latestRawResult = json;
      _resultCount += 1;
      _takingReadings = false;
      _boardResponse = _boardResponseForResult(result);
      _status = result.ok
          ? 'ESP32 predicted ${result.predicted}'
          : 'ESP32 inference error: ${result.error ?? 'unknown'}';
      _addEvent(
        result.ok
            ? 'rep ${result.repetition}: ${result.predicted} ${(result.confidence * 100).toStringAsFixed(1)}%'
            : 'rep ${result.repetition}: inference error',
      );
    });
  }

  void _handleSessionSummary(Map<String, Object?> json) {
    final summary = PcSessionSummary.fromJson(json);
    unawaited(
      _appendMetricsLog(
        (logger, timestamp) => OnboardMetricsLogEntry.sessionSummary(
          sessionId: logger.sessionId,
          isoTimeUtc: timestamp,
          json: json,
        ),
      ),
    );
    setState(() {
      _latestSummary = summary;
      _sessionRunning = false;
      _takingReadings = false;
      _boardResponse =
          'Session complete\n${summary.correctCount}/${summary.totalRepetitions} correct';
      _status =
          'Summary: ${summary.correctCount}/${summary.totalRepetitions} correct';
      _addEvent(_status);
    });
  }

  void _handleStatus(Map<String, Object?> json) {
    setState(() {
      _statusCount += 1;
      final message = (json['message'] ?? json['event'] ?? 'ok').toString();
      _takingReadings = false;
      _boardResponse = message;
      _status = 'ESP32 status: $message';
      _addEvent(_status);
    });
  }

  Future<void> _prepareMetricsLog() async {
    try {
      final logger = await _metricsLoggerFuture;
      final file = await logger.ensureFile();
      if (!mounted) {
        return;
      }
      setState(() => _metricsLogFile = file);
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _status = 'Log setup failed: $error';
        _addEvent('log setup failed');
      });
    }
  }

  Future<void> _appendMetricsLog(
    OnboardMetricsLogEntry Function(
      OnboardMetricsLogger logger,
      DateTime timestamp,
    )
    createEntry,
  ) async {
    try {
      final logger = await _metricsLoggerFuture;
      final file = await logger.append(createEntry(logger, DateTime.now()));
      if (!mounted) {
        return;
      }
      setState(() {
        _metricsLogFile = file;
        _loggedMetricCount += 1;
      });
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _status = 'Log write failed: $error';
        _addEvent('log write failed');
      });
    }
  }

  Future<void> _shareMetricsLog() async {
    if (_sharingLog) {
      return;
    }

    setState(() => _sharingLog = true);
    try {
      final logger = await _metricsLoggerFuture;
      final file = await logger.ensureFile();
      if (!mounted) {
        return;
      }

      setState(() => _metricsLogFile = file);
      await SharePlus.instance.share(
        ShareParams(
          title: 'IMU rehab metrics log',
          subject: 'IMU rehab metrics log',
          text: 'IMU rehab onboard metrics log',
          files: <XFile>[
            XFile(
              file.path,
              mimeType: 'text/csv',
              name: file.uri.pathSegments.last,
            ),
          ],
        ),
      );
    } on Object catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('Could not share log: $error')));
    } finally {
      if (mounted) {
        setState(() => _sharingLog = false);
      }
    }
  }

  BleSensorDevice? get _selectedEsp32 {
    for (final device in _devices) {
      if (device.remoteId == _selectedDeviceId) {
        return device;
      }
    }
    return null;
  }

  void _addEvent(String message) {
    _events.insert(0, _DirectEvent(DateTime.now(), message));
    if (_events.length > 24) {
      _events.removeRange(24, _events.length);
    }
  }

  @override
  Widget build(BuildContext context) {
    final latestResult = _latestResult;
    final latestSummary = _latestSummary;
    return Scaffold(
      appBar: AppBar(
        title: const Text('ESP32 Inference'),
        actions: <Widget>[
          IconButton(
            tooltip: 'Scan for ESP32',
            onPressed: !_connected && !_busy ? _scanForEsp32 : null,
            icon: const Icon(Icons.bluetooth_searching),
          ),
          IconButton(
            tooltip: 'Share metrics log',
            onPressed: _sharingLog ? null : _shareMetricsLog,
            icon: _sharingLog
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.ios_share),
          ),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: <Widget>[
            _Esp32ConnectionPanel(
              devices: _devices,
              gestureMenu: _gestureMenu,
              selectedDeviceId: _selectedDeviceId,
              selectedGesture: _selectedGesture,
              connected: _connected,
              scanning: _scanning,
              busy: _busy,
              sessionRunning: _sessionRunning,
              status: _status,
              scanAdvertisementCount: _scanAdvertisementCount,
              scanNamedCount: _scanNamedCount,
              onDeviceChanged: (value) {
                setState(() => _selectedDeviceId = value);
              },
              onGestureChanged: (value) {
                setState(() => _selectedGesture = value);
              },
              onScan: _scanForEsp32,
              onStopScan: _stopScan,
              onConnect: _connectSelectedEsp32,
              onDisconnect: _disconnectEsp32,
              onRequestMenu: _requestMenu,
              onStartSession: _startSession,
              onStopSession: _stopSession,
              onReset: _resetEsp32Imu,
            ),
            const SizedBox(height: 16),
            _XiaoResponsePanel(
              response: _boardResponse,
              takingReadings: _takingReadings,
            ),
            const SizedBox(height: 16),
            _PredictionPanel(result: latestResult),
            const SizedBox(height: 16),
            _Esp32MetricsPanel(
              connected: _connected,
              sessionRunning: _sessionRunning,
              resultCount: _resultCount,
              statusCount: _statusCount,
              invalidLineCount: _invalidLineCount,
              activeTarget: _activeTarget,
              loggedMetricCount: _loggedMetricCount,
              logFilePath: _metricsLogFile?.path,
              repetitionEvent: _latestRepetitionEvent,
              result: latestResult,
              summary: latestSummary,
              rawResult: _latestRawResult,
            ),
            const SizedBox(height: 16),
            _SummaryPanel(summary: latestSummary),
            const SizedBox(height: 16),
            _EventStreamPanel(events: _events),
          ],
        ),
      ),
    );
  }
}

class _XiaoResponsePanel extends StatelessWidget {
  const _XiaoResponsePanel({
    required this.response,
    required this.takingReadings,
  });

  final String response;
  final bool takingReadings;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final background = takingReadings
        ? const Color(0xFF16A34A)
        : colorScheme.surface;
    final foreground = takingReadings ? Colors.white : colorScheme.onSurface;
    final border = takingReadings
        ? const Color(0xFF15803D)
        : colorScheme.outlineVariant;

    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOut,
      constraints: const BoxConstraints(minHeight: 196),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: border),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.06),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Center(
        child: Text(
          response,
          maxLines: 4,
          overflow: TextOverflow.ellipsis,
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.displaySmall?.copyWith(
            color: foreground,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }
}

class _Esp32ConnectionPanel extends StatelessWidget {
  const _Esp32ConnectionPanel({
    required this.devices,
    required this.gestureMenu,
    required this.selectedDeviceId,
    required this.selectedGesture,
    required this.connected,
    required this.scanning,
    required this.busy,
    required this.sessionRunning,
    required this.status,
    required this.scanAdvertisementCount,
    required this.scanNamedCount,
    required this.onDeviceChanged,
    required this.onGestureChanged,
    required this.onScan,
    required this.onStopScan,
    required this.onConnect,
    required this.onDisconnect,
    required this.onRequestMenu,
    required this.onStartSession,
    required this.onStopSession,
    required this.onReset,
  });

  final List<BleSensorDevice> devices;
  final GestureMenu? gestureMenu;
  final String? selectedDeviceId;
  final String? selectedGesture;
  final bool connected;
  final bool scanning;
  final bool busy;
  final bool sessionRunning;
  final String status;
  final int scanAdvertisementCount;
  final int scanNamedCount;
  final ValueChanged<String?> onDeviceChanged;
  final ValueChanged<String?> onGestureChanged;
  final VoidCallback onScan;
  final VoidCallback onStopScan;
  final VoidCallback onConnect;
  final VoidCallback onDisconnect;
  final VoidCallback onRequestMenu;
  final VoidCallback onStartSession;
  final VoidCallback onStopSession;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    final selectedDeviceValue =
        devices.any((device) => device.remoteId == selectedDeviceId)
        ? selectedDeviceId
        : null;
    final options = gestureMenu?.options ?? const <GestureOption>[];
    final selectedGestureValue =
        options.any((option) => option.selection.toString() == selectedGesture)
        ? selectedGesture
        : null;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Icon(
                  connected ? Icons.bluetooth_connected : Icons.bluetooth,
                  color: connected
                      ? Theme.of(context).colorScheme.primary
                      : Theme.of(context).colorScheme.outline,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    status,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (scanning)
                  const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              'Scan: $scanAdvertisementCount BLE advertisements, $scanNamedCount named',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: ValueKey<String>(selectedDeviceValue ?? 'no-esp32'),
              initialValue: selectedDeviceValue,
              decoration: const InputDecoration(
                labelText: 'ESP32 BLE device',
                border: OutlineInputBorder(),
              ),
              items: devices
                  .map(
                    (device) => DropdownMenuItem<String>(
                      value: device.remoteId,
                      child: Text(
                        '${device.isLikelyEsp32 ? '* ' : ''}${device.name} (${device.rssi} dBm)',
                      ),
                    ),
                  )
                  .toList(growable: false),
              onChanged: connected || busy ? null : onDeviceChanged,
            ),
            const SizedBox(height: 12),
            if (selectedDeviceValue != null)
              Text(
                devices
                    .firstWhere(
                      (device) => device.remoteId == selectedDeviceValue,
                    )
                    .details,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            if (selectedDeviceValue != null) const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: ValueKey<String>(selectedGestureValue ?? 'no-gesture'),
              initialValue: selectedGestureValue,
              decoration: const InputDecoration(
                labelText: 'Gesture task',
                border: OutlineInputBorder(),
              ),
              items: options
                  .map(
                    (option) => DropdownMenuItem<String>(
                      value: option.selection.toString(),
                      child: Text('${option.selection}. ${option.label}'),
                    ),
                  )
                  .toList(growable: false),
              onChanged: connected && !busy && !sessionRunning
                  ? onGestureChanged
                  : null,
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                FilledButton.icon(
                  onPressed: connected || busy ? null : onScan,
                  icon: const Icon(Icons.bluetooth_searching),
                  label: const Text('Scan'),
                ),
                OutlinedButton.icon(
                  onPressed: scanning ? onStopScan : null,
                  icon: const Icon(Icons.stop),
                  label: const Text('Stop Scan'),
                ),
                FilledButton.tonalIcon(
                  onPressed: busy
                      ? null
                      : connected
                      ? onDisconnect
                      : selectedDeviceValue == null
                      ? null
                      : onConnect,
                  icon: Icon(connected ? Icons.link_off : Icons.link),
                  label: Text(connected ? 'Disconnect' : 'Connect'),
                ),
                OutlinedButton.icon(
                  onPressed: connected && !busy && !sessionRunning
                      ? onRequestMenu
                      : null,
                  icon: const Icon(Icons.menu),
                  label: const Text('Menu'),
                ),
                FilledButton.icon(
                  onPressed:
                      connected &&
                          !busy &&
                          !sessionRunning &&
                          selectedGestureValue != null
                      ? onStartSession
                      : null,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Start Session'),
                ),
                OutlinedButton.icon(
                  onPressed: connected && !busy && sessionRunning
                      ? onStopSession
                      : null,
                  icon: const Icon(Icons.pause),
                  label: const Text('Stop Session'),
                ),
                OutlinedButton.icon(
                  onPressed: connected && !busy && !sessionRunning
                      ? onReset
                      : null,
                  icon: const Icon(Icons.restart_alt),
                  label: const Text('Reset IMU'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PredictionPanel extends StatelessWidget {
  const _PredictionPanel({required this.result});

  final PcInferenceResult? result;

  @override
  Widget build(BuildContext context) {
    final current = result;
    final label = current == null
        ? 'Waiting'
        : current.ok
        ? current.predicted
        : 'Inference Error';
    final confidence = current?.confidence ?? 0;
    final detail = current == null
        ? 'Select a gesture task to receive onboard inference results'
        : current.ok
        ? '${(confidence * 100).toStringAsFixed(1)}% confidence'
        : current.error ?? 'Unknown ESP32 inference error';
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'ESP32 Prediction',
              style: Theme.of(context).textTheme.labelLarge,
            ),
            const SizedBox(height: 8),
            Text(label, style: Theme.of(context).textTheme.headlineMedium),
            const SizedBox(height: 8),
            LinearProgressIndicator(value: confidence.clamp(0, 1).toDouble()),
            const SizedBox(height: 8),
            Text(detail),
            const SizedBox(height: 8),
            Text(current == null ? '-' : _correctTrustedText(current)),
            const SizedBox(height: 16),
            for (final classLabel in ModelConstants.labels)
              _ScoreBar(
                label: classLabel,
                value: current?.scores[classLabel] ?? 0,
              ),
          ],
        ),
      ),
    );
  }
}

class _Esp32MetricsPanel extends StatelessWidget {
  const _Esp32MetricsPanel({
    required this.connected,
    required this.sessionRunning,
    required this.resultCount,
    required this.statusCount,
    required this.invalidLineCount,
    required this.activeTarget,
    required this.loggedMetricCount,
    required this.logFilePath,
    required this.repetitionEvent,
    required this.result,
    required this.summary,
    required this.rawResult,
  });

  final bool connected;
  final bool sessionRunning;
  final int resultCount;
  final int statusCount;
  final int invalidLineCount;
  final String activeTarget;
  final int loggedMetricCount;
  final String? logFilePath;
  final PcRepetitionEvent? repetitionEvent;
  final PcInferenceResult? result;
  final PcSessionSummary? summary;
  final Map<String, Object?> rawResult;

  @override
  Widget build(BuildContext context) {
    final collectMs = _rawString(rawResult, 'collect_ms');
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('Metrics', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            _MetricRow('ESP32', connected ? 'connected' : 'disconnected'),
            _MetricRow('Session', sessionRunning ? 'running' : 'idle'),
            _MetricRow('Target', activeTarget.isEmpty ? '-' : activeTarget),
            _MetricRow(
              'Repetition',
              repetitionEvent == null
                  ? result?.repetition.toString() ?? '-'
                  : '${repetitionEvent!.repetition}: ${_labelForEvent(repetitionEvent!.event)}',
            ),
            _MetricRow('Results', resultCount.toString()),
            _MetricRow('Logged rows', loggedMetricCount.toString()),
            _MetricRow('Log file', logFilePath ?? '-'),
            _MetricRow('Status messages', statusCount.toString()),
            _MetricRow('Invalid lines', invalidLineCount.toString()),
            _MetricRow('Device ID', _rawString(rawResult, 'device_id')),
            _MetricRow('IMU', _rawBool(rawResult, 'imu_ok')),
            _MetricRow('Sample count', _rawString(rawResult, 'sample_count')),
            _MetricRow('Collect', collectMs == '-' ? '-' : '$collectMs ms'),
            _MetricRow(
              'Inference',
              result == null
                  ? '-'
                  : '${result!.inferenceMs.toStringAsFixed(3)} ms',
            ),
            _MetricRow(
              'Avg inference',
              summary == null
                  ? '-'
                  : '${summary!.avgInferenceMs.toStringAsFixed(3)} ms',
            ),
          ],
        ),
      ),
    );
  }
}

class _SummaryPanel extends StatelessWidget {
  const _SummaryPanel({required this.summary});

  final PcSessionSummary? summary;

  @override
  Widget build(BuildContext context) {
    final current = summary;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'Session Summary',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            _MetricRow('Target', current?.target ?? '-'),
            _MetricRow(
              'Correct',
              current == null
                  ? '-'
                  : '${current.correctCount}/${current.totalRepetitions} (${(current.accuracy * 100).toStringAsFixed(1)}%)',
            ),
            _MetricRow(
              'Pass',
              current == null
                  ? '-'
                  : '${current.passCount}/${current.totalRepetitions} (${(current.passRate * 100).toStringAsFixed(1)}%)',
            ),
            _MetricRow('Uncertain', current?.uncertainCount.toString() ?? '-'),
            _MetricRow(
              'Avg confidence',
              current == null
                  ? '-'
                  : '${(current.avgPassConfidence * 100).toStringAsFixed(1)}%',
            ),
          ],
        ),
      ),
    );
  }
}

class _EventStreamPanel extends StatelessWidget {
  const _EventStreamPanel({required this.events});

  final List<_DirectEvent> events;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'Recent ESP32 Events',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (events.isEmpty)
              const Text('-')
            else
              for (final event in events.take(10))
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    '${event.time.toLocal().toIso8601String()} | ${event.message}',
                  ),
                ),
          ],
        ),
      ),
    );
  }
}

class _ScoreBar extends StatelessWidget {
  const _ScoreBar({required this.label, required this.value});

  final String label;
  final double value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: <Widget>[
          SizedBox(width: 132, child: Text(label)),
          Expanded(
            child: LinearProgressIndicator(value: value.clamp(0, 1).toDouble()),
          ),
          const SizedBox(width: 8),
          SizedBox(
            width: 52,
            child: Text(
              '${(value * 100).toStringAsFixed(0)}%',
              textAlign: TextAlign.end,
            ),
          ),
        ],
      ),
    );
  }
}

class _MetricRow extends StatelessWidget {
  const _MetricRow(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(width: 132, child: Text(label)),
          Expanded(
            child: Text(value, overflow: TextOverflow.ellipsis, maxLines: 2),
          ),
        ],
      ),
    );
  }
}

class _DirectEvent {
  const _DirectEvent(this.time, this.message);

  final DateTime time;
  final String message;
}

String _labelForEvent(String event) {
  return switch (event) {
    'get_ready' => 'get ready',
    'countdown_3' => 'countdown 3',
    'countdown_2' => 'countdown 2',
    'countdown_1' => 'countdown 1',
    'movement_start' => 'movement start',
    'sampling_finished' => 'sampling finished',
    'next_rep_in_2s' => 'next repetition in 2s',
    _ => event.replaceAll('_', ' '),
  };
}

String _boardResponseForEvent(PcRepetitionEvent event) {
  final message = switch (event.event) {
    'get_ready' => 'Get ready',
    'countdown_3' => '3',
    'countdown_2' => '2',
    'countdown_1' => '1',
    'movement_start' => 'Taking readings',
    'sampling_finished' => 'Sampling finished',
    'next_rep_in_2s' => 'Next repetition soon',
    _ => _labelForEvent(event.event),
  };

  return event.repetition > 0 ? 'Rep ${event.repetition}\n$message' : message;
}

String _boardResponseForResult(PcInferenceResult result) {
  if (!result.ok) {
    return 'Inference error\n${result.error ?? 'Unknown error'}';
  }

  final confidence = (result.confidence * 100).toStringAsFixed(1);
  final prefix = result.repetition > 0 ? 'Rep ${result.repetition}\n' : '';
  return '$prefix${result.predicted}\n$confidence% confidence';
}

String _correctTrustedText(PcInferenceResult result) {
  final correct = result.correct ? 'correct' : 'incorrect';
  final trusted = result.trusted ? 'trusted' : 'low confidence';
  return '$correct | $trusted';
}

String _rawString(Map<String, Object?> json, String key) {
  final value = json[key];
  return value == null ? '-' : value.toString();
}

String _rawBool(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is bool) {
    return value ? 'ok' : 'failed';
  }
  return '-';
}
