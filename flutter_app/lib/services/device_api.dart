import 'dart:convert';
import 'dart:io';

import '../models.dart';

class DeviceApi {
  DeviceApi({HttpClient? client}) : _client = client ?? HttpClient();

  final HttpClient _client;
  String? _authToken;

  void setAuthToken(String? token) => _authToken = token;

  Uri normalizeAddress(String value, {bool statusService = false}) {
    var candidate = value.trim();
    if (!candidate.startsWith('http://') && !candidate.startsWith('https://')) {
      candidate = 'http://$candidate';
    }
    final uri = Uri.tryParse(candidate);
    if (uri == null ||
        uri.host.isEmpty ||
        !{'http', 'https'}.contains(uri.scheme)) {
      throw FormatException(
        statusService
            ? '请输入状态服务地址，例如 192.168.0.107:8787'
            : '请输入设备 IP，例如 192.168.4.1',
      );
    }
    return uri.replace(path: uri.path == '/' ? '' : uri.path);
  }

  Future<DeviceStatus> status(Uri base) async =>
      DeviceStatus.fromJson(await _jsonRequest(base, 'GET', '/status'));

  /// 直播会话快照（sherpa 流式引擎 partial / 定稿句）。
  Future<Map<String, dynamic>> liveFrame(
    Uri base,
    String device,
    String recording,
  ) async =>
      await _jsonRequest(
        base,
        'GET',
        '/live/frame',
        query: {
          'device': device,
          'recording': recording,
        },
      );

  /// BLE 连接性能打点上报（后端仅记录日志）。
  Future<void> postPerf(Uri base, List<String> traceLines) async {
    final payload = '{"trace": [${traceLines.join(",")}]}';
    await _jsonRequest(
      base,
      'POST',
      '/perf',
      body: jsonDecode(payload) as Map<String, dynamic>,
    );
  }

  Future<void> setClock(Uri base) async {
    await _jsonRequest(
      base,
      'POST',
      '/time',
      body: {'epoch': DateTime.now().millisecondsSinceEpoch ~/ 1000},
      acceptedErrors: const {404},
    );
  }

  Future<List<RecordingFile>> files(Uri base) async {
    final json = await _jsonRequest(base, 'GET', '/files');
    return (json['files'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(RecordingFile.fromJson)
        .where((file) => file.name.isNotEmpty)
        .toList();
  }

  Future<void> deleteFile(Uri base, String name) async {
    final request = await _client.deleteUrl(_fileUri(base, name));
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    final response = await request.close().timeout(const Duration(seconds: 12));
    final text = await utf8.decoder.bind(response).join();
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HttpException(
        '设备返回 HTTP ${response.statusCode}${text.isEmpty ? '' : ': $text'}',
      );
    }
  }

  Future<File> downloadFile(
    Uri base,
    String name,
    File destination, {
    void Function(int receivedBytes, int totalBytes)? onProgress,
  }) async {
    Object? lastError;
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        if (await destination.exists()) await destination.delete();
        final request = await _client.getUrl(_fileUri(base, name));
        request.headers.set(HttpHeaders.acceptHeader, 'audio/wav');
        final response = await request.close().timeout(
          const Duration(seconds: 15),
        );
        if (response.statusCode < 200 || response.statusCode >= 300) {
          await response.drain<void>();
          throw HttpException('设备返回 HTTP ${response.statusCode}');
        }
        final expectedLength = response.contentLength;
        var receivedLength = 0;
        onProgress?.call(receivedLength, expectedLength);
        final sink = destination.openWrite();
        try {
          await for (final chunk in response.timeout(
            const Duration(minutes: 5),
          )) {
            sink.add(chunk);
            receivedLength += chunk.length;
            onProgress?.call(receivedLength, expectedLength);
          }
        } finally {
          await sink.close();
        }
        final actualLength = await destination.length();
        if (actualLength < 44 ||
            (expectedLength >= 0 && actualLength != expectedLength)) {
          throw const FileSystemException('录音文件下载不完整');
        }
        onProgress?.call(actualLength, expectedLength);
        return destination;
      } catch (error) {
        lastError = error;
        if (await destination.exists()) await destination.delete();
        if (attempt < 2) {
          await Future<void>.delayed(Duration(seconds: attempt + 1));
        }
      }
    }
    throw lastError ?? const FileSystemException('录音下载失败');
  }

  Future<TranscriptionBackendStatus> transcriptionStatus(Uri base) async =>
      TranscriptionBackendStatus.fromJson(
        await _jsonRequest(base, 'GET', '/api/status'),
      );

  Future<Map<String, dynamic>> workFilter(Uri base, String deviceId) async =>
      _jsonRequest(
        base,
        'GET',
        '/api/devices/${Uri.encodeComponent(deviceId)}/work-filter',
      );

  Future<Map<String, dynamic>> saveWorkFilter(
    Uri base,
    String deviceId,
    WorkFilterProfile profile,
  ) async => _jsonRequest(
    base,
    'POST',
    '/api/devices/${Uri.encodeComponent(deviceId)}/work-filter',
    body: profile.toJson(),
  );

  Future<TranscriptionAsrStatus> selectModel(Uri base, String modelId) async {
    final json = await _jsonRequest(
      base,
      'POST',
      '/api/models/select',
      body: {'model_id': modelId},
    );
    final asr = json['asr'];
    if (asr is! Map<String, dynamic>) {
      throw const FormatException('转写服务没有返回模型状态');
    }
    return TranscriptionAsrStatus.fromJson(asr);
  }

  Future<Map<String, dynamic>> saveCustomAsr(
    Uri base,
    CustomAsrConfig config,
  ) => _jsonRequest(
    base,
    'POST',
    '/api/config/asr',
    body: config.toJson(select: true),
  );

  Future<Map<String, dynamic>> deleteCustomAsr(Uri base, String providerId) =>
      _jsonRequest(
        base,
        'DELETE',
        '/api/config/asr/${Uri.encodeComponent(providerId)}',
      );

  Future<CloudStorageConfig> cloudStorage(Uri base) async =>
      CloudStorageConfig.fromJson(
        (await _jsonRequest(base, 'GET', '/api/config/storage'))['storage']
                as Map<String, dynamic>? ??
            const {},
      );

  Future<Map<String, dynamic>> testCloudStorage(
    Uri base,
    CloudStorageConfig config,
  ) => _jsonRequest(
    base,
    'POST',
    '/api/config/storage/test',
    body: config.toJson(),
  );

  Future<Map<String, dynamic>> saveCloudStorage(
    Uri base,
    CloudStorageConfig config,
  ) => _jsonRequest(base, 'POST', '/api/config/storage', body: config.toJson());

  Future<void> bindRecorder(Uri base, String deviceId) async {
    await _jsonRequest(
      base,
      'POST',
      '/api/mobile/recorder-binding',
      body: {'device_id': deviceId},
    );
  }

  Future<ObsidianSyncStatus> obsidianSyncStatus(Uri base) async =>
      ObsidianSyncStatus.fromJson(
        await _jsonRequest(base, 'GET', '/api/mobile/obsidian-sync'),
      );

  Future<ObsidianSyncPairing> createObsidianSyncPairing(
    Uri base, {
    required String targetName,
  }) async => ObsidianSyncPairing.fromJson(
    await _jsonRequest(
      base,
      'POST',
      '/api/mobile/obsidian-sync/activation',
      body: {'target_name': targetName},
    ),
  );

  Future<void> retryAiProcessing(Uri base, int jobId) async {
    await _jsonRequest(
      base,
      'POST',
      '/api/jobs/$jobId/retry-ai',
      body: const {},
    );
  }

  Future<UnifiedRelease> unifiedRelease(Uri base) async {
    final release = UnifiedRelease.fromJson(
      await _jsonRequest(base, 'GET', '/release.json'),
    );
    if (release.version.isEmpty ||
        release.firmwareVersion.isEmpty ||
        release.firmwareUrl.isEmpty ||
        release.appVersion.isEmpty) {
      throw const FormatException('更新信息不完整');
    }
    return release;
  }

  Future<Map<String, dynamic>> _jsonRequest(
    Uri base,
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
    Set<int> acceptedErrors = const {},
  }) async {
    final resolved = query == null
        ? _resolve(base, path)
        : base.replace(
            path: path,
            queryParameters: query,
          );
    final request = await _client.openUrl(method, resolved);
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (_authToken != null && _authToken!.isNotEmpty) {
      request.headers.set(
        HttpHeaders.authorizationHeader,
        'Bearer ${_authToken!}',
      );
    }
    if (body != null) {
      final encodedBody = utf8.encode(jsonEncode(body));
      request.headers.contentType = ContentType.json;
      request.contentLength = encodedBody.length;
      request.add(encodedBody);
    }
    final response = await request.close().timeout(const Duration(seconds: 12));
    final text = await utf8.decoder.bind(response).join();
    if ((response.statusCode < 200 || response.statusCode >= 300) &&
        !acceptedErrors.contains(response.statusCode)) {
      throw HttpException(
        '设备返回 HTTP ${response.statusCode}${text.isEmpty ? '' : ': $text'}',
      );
    }
    if (text.isEmpty) return const {};
    final decoded = jsonDecode(text);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('服务返回的数据格式不正确');
    }
    return decoded;
  }

  Uri _resolve(Uri base, String path) => base.replace(path: path, query: null);

  Uri _fileUri(Uri base, String name) =>
      base.replace(pathSegments: ['files', name], query: null);

  void close() => _client.close(force: true);
}
