import 'dart:convert';

import 'package:ai_recording_bean/models.dart';
import 'package:ai_recording_bean/services/ble_recorder.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parses BLE recorder status including Wi-Fi address', () {
    final status = BleDeviceStatus.parse(
      'device_id=bean_ye_2;recording=1;seconds=42;uploading=1;upload_sent=512;'
      'upload_total=1024;file=test.wav;sd_free_mb=120.5;'
      'sd_total_mb=256;firmware=0.3.11;ota=idle;wifi=192.168.0.125',
    );

    expect(status.deviceId, 'bean_ye_2');
    expect(status.recording, isTrue);
    expect(status.seconds, 42);
    expect(status.uploadSentBytes, 512);
    expect(status.file, 'test.wav');
    expect(status.sdFreeMb, 120.5);
    expect(status.wifiAddress, '192.168.0.125');
  });

  test('parses BLE cloud storage status', () {
    final status = BleDeviceStatus.parse(
      'device_id=bean_ye_2;cloud=ready;cloud_status=saved;'
      'cloud_config_revision=3',
    );

    expect(status.cloudConfigured, isTrue);
    expect(status.cloudStatus, 'saved');
    expect(status.cloudConfigRevision, 3);
  });

  test('serializes cloud storage config without returning missing secrets', () {
    const config = CloudStorageConfig(
      type: 's3',
      provider: 'aliyun-oss',
      endpoint: 'https://oss.example.com',
      region: 'cn-hangzhou',
      bucket: 'recordingbean',
      accessKey: '',
      secretKey: '',
      accessKeyConfigured: true,
      secretKeyConfigured: true,
    );

    final json = config.toJson();
    final recorder = config.toRecorderJson();

    expect(json['provider'], 'aliyun-oss');
    expect(json.containsKey('access_key'), isFalse);
    expect(json.containsKey('secret_key'), isFalse);
    expect(recorder['type'], 's3');
    expect(recorder.containsKey('access_key'), isFalse);
    expect(recorder.containsKey('secret_key'), isFalse);
  });

  test('uses the backend default storage without serializing credentials', () {
    const config = CloudStorageConfig(
      endpoint: 'http://192.168.0.111:30900',
      accessKeyConfigured: true,
      secretKeyConfigured: true,
      usesBackendDefault: true,
    );

    expect(config.toJson(), {'use_backend_default': true});
  });

  test('parses HTTP recording file response', () {
    final file = RecordingFile.fromJson({
      'name': '会议.wav',
      'size': 32000,
      'duration_seconds': 1,
      'created_at': '2026-07-29 09:00:00',
    });

    expect(file.name, '会议.wav');
    expect(file.size, 32000);
    expect(file.durationSeconds, 1);
  });

  test('parses the file name while the recorder is uploading', () {
    final status = BleDeviceStatus.parse(
      'recording=0;seconds=0;uploading=1;upload_sent=1024;'
      'upload_total=4096;file=REC_20260729_112900_0.wav',
    );

    expect(status.recording, isFalse);
    expect(status.uploading, isTrue);
    expect(status.file, 'REC_20260729_112900_0.wav');
  });

  test('parses BLE file event returned by the recorder', () {
    final event = BleRecorderService.decodeFileEvent(
      utf8.encode(
        '{"t":"file","n":"测试录音.wav","s":320044,'
        '"d":10,"c":"2026-07-29T10:44:00"}',
      ),
    );

    expect(event, isA<BleFileFound>());
    final file = (event as BleFileFound).file;
    expect(file.name, '测试录音.wav');
    expect(file.size, 320044);
    expect(file.durationSeconds, 10);
    expect(file.createdAt, '2026-07-29T10:44:00');
  });

  test('parses health state for every ASR model', () {
    final status = TranscriptionAsrStatus.fromJson({
      'selected_model_id': 'qwen3-asr',
      'models': [
        {
          'id': 'zhipu-glm-asr',
          'name': '智谱 GLM-ASR',
          'configured': true,
          'selected': false,
          'healthy': true,
        },
        {
          'id': 'qwen3-asr',
          'name': '阿里 Qwen3-ASR',
          'configured': true,
          'selected': true,
          'healthy': false,
        },
      ],
    });

    expect(status.models.first.healthy, isTrue);
    expect(status.models.last.healthy, isFalse);
  });

  test('serializes complete custom ASR configuration', () {
    const config = CustomAsrConfig(
      id: 'asr-test',
      enabled: true,
      name: '内部 ASR',
      baseUrl: 'https://asr.example.com',
      healthPath: '/ready',
      transcribePath: '/v1/transcribe',
      timeoutSeconds: 90,
      fileField: 'audio',
      languageField: 'locale',
      promptField: 'context',
      responseTextPath: 'result.text',
      responseLanguagePath: 'result.language',
      responseModelPath: 'meta.model',
      responseSegmentsPath: 'result.segments',
      apiKey: 'secret',
      apiKeyHeader: 'X-API-Key',
      apiKeyPrefix: '',
      headersJson: '{"X-Workspace":"mobile"}',
    );

    final json = config.toJson(select: true);

    expect(json['select'], isTrue);
    expect(json['id'], 'asr-test');
    expect(json['timeout_seconds'], 90);
    expect(json['prompt_field'], 'context');
    expect(json['response_text_path'], 'result.text');
    expect(json['response_segments_path'], 'result.segments');
    expect(json['headers_json'], '{"X-Workspace":"mobile"}');
    expect(json['api_key'], 'secret');
  });

  test('parses multiple custom ASR services from the backend status', () {
    final status = TranscriptionBackendStatus.fromJson({
      'counts': {},
      'jobs': [],
      'asr': {
        'selected_model_id': 'custom-asr:asr-a',
        'models': [],
        'custom_asrs': [
          {
            'id': 'asr-a',
            'enabled': true,
            'name': '文本服务',
            'base_url': 'https://text.example.test',
          },
          {
            'id': 'asr-b',
            'enabled': false,
            'name': '备用服务',
            'base_url': 'https://backup.example.test',
          },
        ],
      },
    });

    expect(status.customAsrs, hasLength(2));
    expect(status.customAsrs.first.id, 'asr-a');
    expect(status.customAsr.name, '文本服务');
  });

  test('parses Obsidian target status and a replacement pairing', () {
    final status = ObsidianSyncStatus.fromJson({
      'bound': true,
      'targets': [
        {
          'label': 'Obsidian: Work Vault',
          'created_at': '2026-08-15T04:00:00Z',
          'last_used_at': '2026-08-15T04:01:00Z',
        },
      ],
    });
    final pairing = ObsidianSyncPairing.fromJson({
      'device_id': 'bean_ye_2',
      'activation_code': 'ARB-example',
      'expires_at': '2026-08-15T04:15:00Z',
      'replaces_previous': true,
    });

    expect(status.bound, isTrue);
    expect(status.targets.single.label, 'Obsidian: Work Vault');
    expect(pairing.activationCode, 'ARB-example');
    expect(pairing.replacesPrevious, isTrue);
  });

  test('parses separate Android and iOS app update URLs', () {
    final release = UnifiedRelease.fromJson({
      'version': '0.5.4',
      'firmware': {
        'version': '0.3.7',
        'url': 'http://192.168.0.107:8790/firmware/0.3.7.bin',
      },
      'app': {
        'version': '0.5.4',
        'url': 'http://192.168.0.107:8790/app/0.5.4.apk',
        'ios_url': 'https://testflight.apple.com/join/example',
      },
    });

    expect(release.appUrl, endsWith('/app/0.5.4.apk'));
    expect(release.iosAppUrl, startsWith('https://testflight.apple.com/'));
  });

  test('does not reuse an old completed job for a known current recording', () {
    const oldJob = TranscriptionJobStatus(
      deviceId: 'bean_ye_2',
      recordingName: 'REC_20260729_173235_2.wav',
      status: 'completed',
      attempts: 1,
      updatedAt: '2026-07-29T09:33:02Z',
      error: '',
      noteSynced: true,
    );

    final matched = matchWorkflowJob(
      jobs: const [oldJob],
      recordingName: 'REC_20260729_173608_3.wav',
      baselineRecordingNames: const {},
      workflowActive: true,
    );

    expect(matched, isNull);
  });

  test(
    'work filter profile supports meeting scene and old profiles default to work',
    () {
      final meeting = WorkFilterProfile.fromJson({
        'enabled': true,
        'recording_scene': 'meeting',
        'work_types': ['software_development', 'project'],
      });
      final old = WorkFilterProfile.fromJson({'enabled': true});

      expect(meeting.recordingScene, 'meeting');
      expect(
        meeting.workTypes,
        containsAll(['software_development', 'project']),
      );
      expect(old.recordingScene, 'work');
      expect(old.toJson()['recording_scene'], 'work');
    },
  );

  test('matches the current recording once its backend job appears', () {
    const currentJob = TranscriptionJobStatus(
      deviceId: 'bean_ye_2',
      recordingName: 'REC_20260729_173608_3.wav',
      status: 'processing',
      attempts: 1,
      updatedAt: '2026-07-29T09:36:50Z',
      error: '',
      noteSynced: false,
    );

    final matched = matchWorkflowJob(
      jobs: const [currentJob],
      recordingName: currentJob.recordingName,
      baselineRecordingNames: const {},
      workflowActive: true,
    );

    expect(matched, same(currentJob));
  });

  test('parses AI processing stage and stage durations', () {
    final job = TranscriptionJobStatus.fromJson({
      'job_id': 42,
      'device_id': 'bean_ye_1',
      'recording_name': 'meeting.wav',
      'status': 'filtering',
      'attempts': 1,
      'ai_attempts': 1,
      'ai_stage': 'filtering',
      'ai_error': 'AI模型输出达到上限',
      'stage_started_at': '2026-08-06T06:00:00Z',
      'updated_at': '2026-08-06T06:00:00Z',
      'error': '',
      'note_synced': false,
      'durations': {
        'download': 1.2,
        'asr': 12.5,
        'optimize': 30.4,
        'filter': 0,
      },
    });

    expect(job.jobId, 42);
    expect(job.status, 'filtering');
    expect(job.aiAttempts, 1);
    expect(job.aiError, 'AI模型输出达到上限');
    expect(job.durations.asr, 12.5);
    expect(job.durations.optimize, 30.4);
  });

  test('keeps workflow elapsed time monotonic between backend refreshes', () {
    const job = TranscriptionJobStatus(
      deviceId: 'bean_ye_2',
      recordingName: 'REC_20260814_101110_0.wav',
      status: 'optimizing',
      attempts: 1,
      updatedAt: '2026-08-14T02:10:00Z',
      error: '',
      noteSynced: false,
      stageStartedAt: '2026-08-14T02:10:00Z',
      stageElapsedSeconds: 8,
    );

    expect(
      liveStageElapsedSeconds(job, DateTime.utc(2026, 8, 14, 2, 10, 11)),
      11,
    );
    expect(
      liveStageElapsedSeconds(job, DateTime.utc(2026, 8, 14, 2, 10, 4)),
      8,
    );
    expect(formatElapsedSeconds(119.9), '1分59秒');
    expect(formatElapsedSeconds(120), '2分00秒');
  });
}
