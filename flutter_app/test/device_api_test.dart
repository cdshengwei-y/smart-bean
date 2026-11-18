import 'dart:convert';
import 'dart:io';

import 'package:ai_recording_bean/services/device_api.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'downloadFile preserves spaces and Chinese characters in file names',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final temporary = await Directory.systemTemp.createTemp('bean-download-');
      final destination = File('${temporary.path}/recording.wav');
      final audio = List<int>.generate(64, (index) => index);
      final progress = <List<int>>[];
      final requestHandled = server.first.then((request) async {
        expect(request.uri.pathSegments, ['files', '测试 录音.wav']);
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType('audio', 'wav')
          ..contentLength = audio.length
          ..add(audio);
        await request.response.close();
      });
      final api = DeviceApi();

      try {
        final result = await api.downloadFile(
          Uri.parse('http://127.0.0.1:${server.port}'),
          '测试 录音.wav',
          destination,
          onProgress: (received, total) => progress.add([received, total]),
        );
        await requestHandled;
        expect(await result.readAsBytes(), audio);
        expect(progress, isNotEmpty);
        expect(progress.last, [audio.length, audio.length]);
      } finally {
        api.close();
        await server.close(force: true);
        await temporary.delete(recursive: true);
      }
    },
  );

  test('retryAiProcessing posts to the job-specific endpoint', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final requestHandled = server.first.then((request) async {
      expect(request.method, 'POST');
      expect(request.uri.path, '/api/jobs/42/retry-ai');
      await utf8.decoder.bind(request).join();
      request.response
        ..statusCode = HttpStatus.ok
        ..headers.contentType = ContentType.json
        ..write('{"job_id":42,"status":"ai_pending"}');
      await request.response.close();
    });
    final api = DeviceApi();

    try {
      await api.retryAiProcessing(
        Uri.parse('http://127.0.0.1:${server.port}'),
        42,
      );
      await requestHandled;
    } finally {
      api.close();
      await server.close(force: true);
    }
  });
}
