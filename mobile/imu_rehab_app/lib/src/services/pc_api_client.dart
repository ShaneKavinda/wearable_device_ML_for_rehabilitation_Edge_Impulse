import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/pc_api_models.dart';

typedef WebSocketFactory = WebSocketChannel Function(Uri uri);

class PcApiClient {
  PcApiClient({
    required Uri baseUri,
    http.Client? httpClient,
    WebSocketFactory? webSocketFactory,
  }) : baseUri = normalizeBaseUri(baseUri),
       _httpClient = httpClient ?? http.Client(),
       _webSocketFactory = webSocketFactory ?? WebSocketChannel.connect;

  final Uri baseUri;
  final http.Client _httpClient;
  final WebSocketFactory _webSocketFactory;

  static Uri normalizeBaseUriText(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) {
      throw const FormatException('API URL must include a host.');
    }
    final withScheme = trimmed.contains('://') ? trimmed : 'http://$trimmed';
    return normalizeBaseUri(Uri.parse(withScheme));
  }

  static Uri normalizeBaseUri(Uri uri) {
    final raw = uri.toString();
    if (!raw.contains('://')) {
      uri = Uri.parse('http://$raw');
    }
    if (uri.scheme != 'http' && uri.scheme != 'https') {
      throw const FormatException(
        'API URL must start with http:// or https://.',
      );
    }
    if (uri.host.isEmpty) {
      throw const FormatException('API URL must include a host.');
    }
    return uri.replace(path: _trimTrailingSlash(uri.path), query: '');
  }

  Stream<PcApiEnvelope> connectResults() {
    final channel = _webSocketFactory(_wsUri('/ws/results'));
    return decodeEnvelopeStream(channel.stream);
  }

  Future<PcHubState> fetchState() async {
    final json = await _getJson('/api/state');
    return PcHubState.fromJson(json);
  }

  Future<PcHubState> updateConfig({
    required String sourceType,
    required String port,
    required int baud,
    required bool saveInvalid,
    required String logDir,
  }) async {
    final json = await _sendJson('PUT', '/api/config', <String, Object?>{
      'source_type': sourceType,
      'port': port,
      'baud': baud,
      'save_invalid': saveInvalid,
      'log_dir': logDir,
    });
    return PcHubState.fromJson(json);
  }

  Future<PcHubState> connectSource() async {
    return PcHubState.fromJson(await _sendJson('POST', '/api/source/connect'));
  }

  Future<PcHubState> disconnectSource() async {
    return PcHubState.fromJson(
      await _sendJson('POST', '/api/source/disconnect'),
    );
  }

  Future<void> startSession(Object gesture) async {
    await _sendJson('POST', '/api/session/start', <String, Object?>{
      'gesture': gesture.toString(),
    });
  }

  Future<PcHubState> stopSession() async {
    return PcHubState.fromJson(await _sendJson('POST', '/api/session/stop'));
  }

  void close() {
    _httpClient.close();
  }

  Future<Map<String, Object?>> _getJson(String path) async {
    final response = await _httpClient.get(_apiUri(path));
    return _decodeResponse(response);
  }

  Future<Map<String, Object?>> _sendJson(
    String method,
    String path, [
    Map<String, Object?>? body,
  ]) async {
    final request = http.Request(method, _apiUri(path));
    request.headers['content-type'] = 'application/json';
    if (body != null) {
      request.body = jsonEncode(body);
    }
    final streamedResponse = await _httpClient.send(request);
    final response = await http.Response.fromStream(streamedResponse);
    return _decodeResponse(response);
  }

  Map<String, Object?> _decodeResponse(http.Response response) {
    final decoded = response.body.isEmpty
        ? <String, Object?>{}
        : jsonDecode(response.body);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      if (decoded is Map && decoded['detail'] != null) {
        throw PcApiException(decoded['detail'].toString());
      }
      throw PcApiException('API request failed: HTTP ${response.statusCode}');
    }
    if (decoded is! Map) {
      throw const FormatException('API response must be a JSON object.');
    }
    return decoded.map<String, Object?>(
      (key, value) => MapEntry(key.toString(), value),
    );
  }

  Uri _apiUri(String path) {
    final prefix = baseUri.path == '/' ? '' : baseUri.path;
    return baseUri.replace(path: '$prefix$path');
  }

  Uri _wsUri(String path) {
    final prefix = baseUri.path == '/' ? '' : baseUri.path;
    return baseUri.replace(
      scheme: baseUri.scheme == 'https' ? 'wss' : 'ws',
      path: '$prefix$path',
    );
  }
}

Stream<PcApiEnvelope> decodeEnvelopeStream(Stream<dynamic> stream) {
  return stream.map((message) {
    if (message is String) {
      return PcApiEnvelope.fromText(message);
    }
    if (message is List<int>) {
      return PcApiEnvelope.fromText(utf8.decode(message));
    }
    throw FormatException(
      'Unsupported WebSocket message type: ${message.runtimeType}',
    );
  });
}

class PcApiException implements Exception {
  const PcApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

String _trimTrailingSlash(String path) {
  if (path == '/' || path.isEmpty) {
    return '';
  }
  return path.endsWith('/') ? path.substring(0, path.length - 1) : path;
}
