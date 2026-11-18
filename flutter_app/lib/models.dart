class DeviceStatus {
  const DeviceStatus({
    this.deviceId = '',
    this.deviceName = 'AI Recording Bean',
    this.firmware = '--',
    this.recording = false,
    this.batteryPercent,
    this.sdFreeMb,
    this.sdTotalMb,
    this.currentFile = '',
    this.recordingSeconds = 0,
    this.wifiRssiDbm,
    this.uploadInProgress = false,
    this.lastUploadError = '',
  });

  factory DeviceStatus.fromJson(Map<String, dynamic> json) => DeviceStatus(
    deviceId: json['device_id'] as String? ?? '',
    deviceName: json['device_name'] as String? ?? 'AI Recording Bean',
    firmware: json['firmware'] as String? ?? '--',
    recording: json['recording'] as bool? ?? false,
    batteryPercent: (json['battery_percent'] as num?)?.toInt(),
    sdFreeMb: (json['sd_free_mb'] as num?)?.toDouble(),
    sdTotalMb: (json['sd_total_mb'] as num?)?.toDouble(),
    currentFile: json['current_file'] as String? ?? '',
    recordingSeconds: (json['recording_seconds'] as num?)?.toInt() ?? 0,
    wifiRssiDbm: (json['wifi_rssi_dbm'] as num?)?.toInt(),
    uploadInProgress: json['upload_in_progress'] as bool? ?? false,
    lastUploadError: json['last_upload_error'] as String? ?? '',
  );

  final String deviceId;
  final String deviceName;
  final String firmware;
  final bool recording;
  final int? batteryPercent;
  final double? sdFreeMb;
  final double? sdTotalMb;
  final String currentFile;
  final int recordingSeconds;
  final int? wifiRssiDbm;
  final bool uploadInProgress;
  final String lastUploadError;
}

class RecordingFile {
  const RecordingFile({
    required this.name,
    required this.size,
    required this.durationSeconds,
    required this.createdAt,
  });

  factory RecordingFile.fromJson(Map<String, dynamic> json) => RecordingFile(
    name: json['name'] as String? ?? json['n'] as String? ?? '',
    size: (json['size'] as num? ?? json['s'] as num?)?.toInt() ?? 0,
    durationSeconds:
        (json['duration_seconds'] as num? ?? json['d'] as num?)?.toInt() ?? 0,
    createdAt: json['created_at'] as String? ?? json['c'] as String? ?? '--',
  );

  final String name;
  final int size;
  final int durationSeconds;
  final String createdAt;
}

class TranscriptionJobStatus {
  const TranscriptionJobStatus({
    required this.deviceId,
    required this.recordingName,
    required this.status,
    required this.attempts,
    required this.updatedAt,
    required this.error,
    required this.noteSynced,
    this.jobId = 0,
    this.aiAttempts = 0,
    this.aiStage = '',
    this.aiError = '',
    this.stageStartedAt = '',
    this.stageElapsedSeconds = 0,
    this.durations = const ProcessingDurations(),
  });

  factory TranscriptionJobStatus.fromJson(Map<String, dynamic> json) =>
      TranscriptionJobStatus(
        deviceId: json['device_id'] as String? ?? 'legacy',
        recordingName: json['recording_name'] as String? ?? '',
        status: json['status'] as String? ?? 'pending',
        attempts: (json['attempts'] as num?)?.toInt() ?? 0,
        updatedAt: json['updated_at'] as String? ?? '',
        error: json['error'] as String? ?? '',
        noteSynced: json['note_synced'] as bool? ?? false,
        jobId: (json['job_id'] as num?)?.toInt() ?? 0,
        aiAttempts: (json['ai_attempts'] as num?)?.toInt() ?? 0,
        aiStage: json['ai_stage'] as String? ?? '',
        aiError: json['ai_error'] as String? ?? '',
        stageStartedAt: json['stage_started_at'] as String? ?? '',
        stageElapsedSeconds:
            (json['stage_elapsed_seconds'] as num?)?.toDouble() ?? 0,
        durations: ProcessingDurations.fromJson(
          json['durations'] as Map<String, dynamic>? ?? const {},
        ),
      );

  final String deviceId;
  final String recordingName;
  final String status;
  final int attempts;
  final String updatedAt;
  final String error;
  final bool noteSynced;
  final int jobId;
  final int aiAttempts;
  final String aiStage;
  final String aiError;
  final String stageStartedAt;
  final double stageElapsedSeconds;
  final ProcessingDurations durations;
}

double liveStageElapsedSeconds(TranscriptionJobStatus job, DateTime now) {
  final startedAt = DateTime.tryParse(job.stageStartedAt);
  if (startedAt == null) return job.stageElapsedSeconds;
  final localElapsed =
      now.toUtc().difference(startedAt.toUtc()).inMilliseconds / 1000;
  return localElapsed > job.stageElapsedSeconds
      ? localElapsed
      : job.stageElapsedSeconds;
}

String formatElapsedSeconds(double seconds) {
  final roundedDown = seconds.isFinite ? seconds.floor() : 0;
  final totalSeconds = roundedDown < 0 ? 0 : roundedDown;
  if (totalSeconds < 60) return '$totalSeconds秒';
  final minutes = totalSeconds ~/ 60;
  final remainder = totalSeconds % 60;
  return '$minutes分${remainder.toString().padLeft(2, '0')}秒';
}

class ProcessingDurations {
  const ProcessingDurations({
    this.download = 0,
    this.asr = 0,
    this.rawNote = 0,
    this.optimize = 0,
    this.filter = 0,
    this.finalNote = 0,
  });

  factory ProcessingDurations.fromJson(Map<String, dynamic> json) =>
      ProcessingDurations(
        download: (json['download'] as num?)?.toDouble() ?? 0,
        asr: (json['asr'] as num?)?.toDouble() ?? 0,
        rawNote: (json['raw_note'] as num?)?.toDouble() ?? 0,
        optimize: (json['optimize'] as num?)?.toDouble() ?? 0,
        filter: (json['filter'] as num?)?.toDouble() ?? 0,
        finalNote: (json['final_note'] as num?)?.toDouble() ?? 0,
      );

  final double download;
  final double asr;
  final double rawNote;
  final double optimize;
  final double filter;
  final double finalNote;
}

class TranscriptionAsrModel {
  const TranscriptionAsrModel({
    required this.id,
    required this.name,
    required this.description,
    required this.configured,
    required this.selected,
    required this.healthy,
  });

  factory TranscriptionAsrModel.fromJson(Map<String, dynamic> json) =>
      TranscriptionAsrModel(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? json['id'] as String? ?? '',
        description: json['description'] as String? ?? '',
        configured: json['configured'] as bool? ?? false,
        selected: json['selected'] as bool? ?? false,
        healthy: json['healthy'] as bool?,
      );

  final String id;
  final String name;
  final String description;
  final bool configured;
  final bool selected;
  final bool? healthy;
}

class TranscriptionAsrStatus {
  const TranscriptionAsrStatus({
    required this.selectedModelId,
    required this.models,
  });

  factory TranscriptionAsrStatus.fromJson(Map<String, dynamic> json) =>
      TranscriptionAsrStatus(
        selectedModelId: json['selected_model_id'] as String? ?? '',
        models: (json['models'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(TranscriptionAsrModel.fromJson)
            .where((model) => model.id.isNotEmpty)
            .toList(),
      );

  final String selectedModelId;
  final List<TranscriptionAsrModel> models;
}

class CustomAsrConfig {
  const CustomAsrConfig({
    this.id = '',
    this.enabled = false,
    this.name = '自定义 HTTP ASR',
    this.description = '用户配置的 multipart HTTP 转写服务',
    this.baseUrl = '',
    this.healthPath = '/health',
    this.transcribePath = '/transcribe',
    this.timeoutSeconds = 600,
    this.fileField = 'file',
    this.languageField = 'language',
    this.promptField = 'prompt',
    this.responseTextPath = 'text',
    this.responseLanguagePath = 'language',
    this.responseModelPath = 'model',
    this.responseSegmentsPath = 'segments',
    this.apiKeyConfigured = false,
    this.apiKey = '',
    this.apiKeyHeader = 'Authorization',
    this.apiKeyPrefix = 'Bearer ',
    this.headersJson = '',
  });

  factory CustomAsrConfig.fromJson(Map<String, dynamic> json) =>
      CustomAsrConfig(
        id: json['id'] as String? ?? '',
        enabled: json['enabled'] as bool? ?? false,
        name: json['name'] as String? ?? '自定义 HTTP ASR',
        description:
            json['description'] as String? ?? '用户配置的 multipart HTTP 转写服务',
        baseUrl: json['base_url'] as String? ?? '',
        healthPath: json['health_path'] as String? ?? '/health',
        transcribePath: json['transcribe_path'] as String? ?? '/transcribe',
        timeoutSeconds: (json['timeout_seconds'] as num?)?.toDouble() ?? 600,
        fileField: json['file_field'] as String? ?? 'file',
        languageField: json['language_field'] as String? ?? 'language',
        promptField: json['prompt_field'] as String? ?? 'prompt',
        responseTextPath: json['response_text_path'] as String? ?? 'text',
        responseLanguagePath:
            json['response_language_path'] as String? ?? 'language',
        responseModelPath: json['response_model_path'] as String? ?? 'model',
        responseSegmentsPath:
            json['response_segments_path'] as String? ?? 'segments',
        apiKeyConfigured: json['api_key_configured'] as bool? ?? false,
        apiKey: json['api_key'] as String? ?? '',
        apiKeyHeader: json['api_key_header'] as String? ?? 'Authorization',
        apiKeyPrefix: json['api_key_prefix'] as String? ?? 'Bearer ',
        headersJson: json['headers_json'] as String? ?? '',
      );

  final String id;
  final bool enabled;
  final String name;
  final String description;
  final String baseUrl;
  final String healthPath;
  final String transcribePath;
  final double timeoutSeconds;
  final String fileField;
  final String languageField;
  final String promptField;
  final String responseTextPath;
  final String responseLanguagePath;
  final String responseModelPath;
  final String responseSegmentsPath;
  final bool apiKeyConfigured;
  final String apiKey;
  final String apiKeyHeader;
  final String apiKeyPrefix;
  final String headersJson;

  Map<String, dynamic> toJson({bool select = false}) => {
    if (id.isNotEmpty) 'id': id,
    'enabled': enabled,
    'name': name,
    'description': description,
    'base_url': baseUrl,
    'health_path': healthPath,
    'transcribe_path': transcribePath,
    'timeout_seconds': timeoutSeconds,
    'file_field': fileField,
    'language_field': languageField,
    'prompt_field': promptField,
    'response_text_path': responseTextPath,
    'response_language_path': responseLanguagePath,
    'response_model_path': responseModelPath,
    'response_segments_path': responseSegmentsPath,
    'api_key_header': apiKeyHeader,
    'api_key_prefix': apiKeyPrefix,
    'headers_json': headersJson,
    if (apiKey.isNotEmpty) 'api_key': apiKey,
    'select': select,
  };

  CustomAsrConfig copyWith({
    String? id,
    bool? enabled,
    String? name,
    String? description,
    String? baseUrl,
    String? healthPath,
    String? transcribePath,
    double? timeoutSeconds,
    String? fileField,
    String? languageField,
    String? promptField,
    String? responseTextPath,
    String? responseLanguagePath,
    String? responseModelPath,
    String? responseSegmentsPath,
    bool? apiKeyConfigured,
    String? apiKey,
    String? apiKeyHeader,
    String? apiKeyPrefix,
    String? headersJson,
  }) => CustomAsrConfig(
    id: id ?? this.id,
    enabled: enabled ?? this.enabled,
    name: name ?? this.name,
    description: description ?? this.description,
    baseUrl: baseUrl ?? this.baseUrl,
    healthPath: healthPath ?? this.healthPath,
    transcribePath: transcribePath ?? this.transcribePath,
    timeoutSeconds: timeoutSeconds ?? this.timeoutSeconds,
    fileField: fileField ?? this.fileField,
    languageField: languageField ?? this.languageField,
    promptField: promptField ?? this.promptField,
    responseTextPath: responseTextPath ?? this.responseTextPath,
    responseLanguagePath: responseLanguagePath ?? this.responseLanguagePath,
    responseModelPath: responseModelPath ?? this.responseModelPath,
    responseSegmentsPath: responseSegmentsPath ?? this.responseSegmentsPath,
    apiKeyConfigured: apiKeyConfigured ?? this.apiKeyConfigured,
    apiKey: apiKey ?? this.apiKey,
    apiKeyHeader: apiKeyHeader ?? this.apiKeyHeader,
    apiKeyPrefix: apiKeyPrefix ?? this.apiKeyPrefix,
    headersJson: headersJson ?? this.headersJson,
  );
}

class CloudStorageConfig {
  const CloudStorageConfig({
    this.type = 's3',
    this.provider = 'minio',
    this.endpoint = '',
    this.region = 'us-east-1',
    this.bucket = 'recordingbean',
    this.recordingsPrefix = 'recordings/',
    this.notesPrefix = 'notes/',
    this.accessKey = '',
    this.secretKey = '',
    this.tlsCaCert = '',
    this.adapterFactory = '',
    this.adapterOptionsJson = '',
    this.accessKeyConfigured = false,
    this.secretKeyConfigured = false,
    this.adapterOptionsConfigured = false,
    this.recorderSyncSupported = true,
    this.source = '',
    this.usesBackendDefault = false,
  });

  factory CloudStorageConfig.fromJson(Map<String, dynamic> json) =>
      CloudStorageConfig(
        type: json['type'] as String? ?? 's3',
        provider: json['provider'] as String? ?? 'minio',
        endpoint: json['endpoint'] as String? ?? '',
        region: json['region'] as String? ?? 'us-east-1',
        bucket: json['bucket'] as String? ?? 'recordingbean',
        recordingsPrefix: json['recordings_prefix'] as String? ?? 'recordings/',
        notesPrefix: json['notes_prefix'] as String? ?? 'notes/',
        accessKey: json['access_key'] as String? ?? '',
        secretKey: json['secret_key'] as String? ?? '',
        tlsCaCert: json['tls_ca_cert'] as String? ?? '',
        adapterFactory: json['adapter_factory'] as String? ?? '',
        adapterOptionsJson: json['adapter_options_json'] as String? ?? '',
        accessKeyConfigured: json['access_key_configured'] as bool? ?? false,
        secretKeyConfigured: json['secret_key_configured'] as bool? ?? false,
        adapterOptionsConfigured:
            json['adapter_options_configured'] as bool? ?? false,
        recorderSyncSupported: json['recorder_sync_supported'] as bool? ?? true,
        source: json['source'] as String? ?? '',
        usesBackendDefault: json['uses_backend_default'] as bool? ?? false,
      );

  final String type;
  final String provider;
  final String endpoint;
  final String region;
  final String bucket;
  final String recordingsPrefix;
  final String notesPrefix;
  final String accessKey;
  final String secretKey;
  final String tlsCaCert;
  final String adapterFactory;
  final String adapterOptionsJson;
  final bool accessKeyConfigured;
  final bool secretKeyConfigured;
  final bool adapterOptionsConfigured;
  final bool recorderSyncSupported;
  final String source;
  final bool usesBackendDefault;

  bool get isS3 => type == 's3';

  Map<String, dynamic> toJson() {
    if (usesBackendDefault) return const {'use_backend_default': true};
    return {
      'type': type,
      'provider': provider,
      'endpoint': endpoint,
      'region': region,
      'bucket': bucket,
      'recordings_prefix': recordingsPrefix,
      'notes_prefix': notesPrefix,
      if (accessKey.isNotEmpty) 'access_key': accessKey,
      if (secretKey.isNotEmpty) 'secret_key': secretKey,
      if (tlsCaCert.isNotEmpty) 'tls_ca_cert': tlsCaCert,
      if (type == 'custom') ...{
        'adapter_factory': adapterFactory,
        if (adapterOptionsJson.isNotEmpty)
          'adapter_options_json': adapterOptionsJson,
      },
    };
  }

  Map<String, dynamic> toRecorderJson() => {
    'type': 's3',
    'provider': provider,
    'endpoint': endpoint,
    'region': region,
    'bucket': bucket,
    'recordings_prefix': recordingsPrefix,
    'notes_prefix': notesPrefix,
    if (accessKey.isNotEmpty) 'access_key': accessKey,
    if (secretKey.isNotEmpty) 'secret_key': secretKey,
    if (tlsCaCert.isNotEmpty) 'tls_ca_cert': tlsCaCert,
  };

  CloudStorageConfig copyWith({
    String? type,
    String? provider,
    String? endpoint,
    String? region,
    String? bucket,
    String? recordingsPrefix,
    String? notesPrefix,
    String? accessKey,
    String? secretKey,
    String? tlsCaCert,
    String? adapterFactory,
    String? adapterOptionsJson,
    bool? accessKeyConfigured,
    bool? secretKeyConfigured,
    bool? adapterOptionsConfigured,
    bool? recorderSyncSupported,
    String? source,
    bool? usesBackendDefault,
  }) => CloudStorageConfig(
    type: type ?? this.type,
    provider: provider ?? this.provider,
    endpoint: endpoint ?? this.endpoint,
    region: region ?? this.region,
    bucket: bucket ?? this.bucket,
    recordingsPrefix: recordingsPrefix ?? this.recordingsPrefix,
    notesPrefix: notesPrefix ?? this.notesPrefix,
    accessKey: accessKey ?? this.accessKey,
    secretKey: secretKey ?? this.secretKey,
    tlsCaCert: tlsCaCert ?? this.tlsCaCert,
    adapterFactory: adapterFactory ?? this.adapterFactory,
    adapterOptionsJson: adapterOptionsJson ?? this.adapterOptionsJson,
    accessKeyConfigured: accessKeyConfigured ?? this.accessKeyConfigured,
    secretKeyConfigured: secretKeyConfigured ?? this.secretKeyConfigured,
    adapterOptionsConfigured:
        adapterOptionsConfigured ?? this.adapterOptionsConfigured,
    recorderSyncSupported: recorderSyncSupported ?? this.recorderSyncSupported,
    source: source ?? this.source,
    usesBackendDefault: usesBackendDefault ?? this.usesBackendDefault,
  );
}

class ObsidianSyncTarget {
  const ObsidianSyncTarget({
    required this.label,
    required this.createdAt,
    required this.lastUsedAt,
  });

  factory ObsidianSyncTarget.fromJson(Map<String, dynamic> json) =>
      ObsidianSyncTarget(
        label: json['label'] as String? ?? '',
        createdAt: json['created_at'] as String? ?? '',
        lastUsedAt: json['last_used_at'] as String? ?? '',
      );

  final String label;
  final String createdAt;
  final String lastUsedAt;
}

class ObsidianSyncStatus {
  const ObsidianSyncStatus({required this.bound, required this.targets});

  factory ObsidianSyncStatus.fromJson(Map<String, dynamic> json) =>
      ObsidianSyncStatus(
        bound: json['bound'] as bool? ?? false,
        targets: (json['targets'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(ObsidianSyncTarget.fromJson)
            .toList(),
      );

  final bool bound;
  final List<ObsidianSyncTarget> targets;
}

class ObsidianSyncPairing {
  const ObsidianSyncPairing({
    required this.deviceId,
    required this.activationCode,
    required this.expiresAt,
    required this.replacesPrevious,
  });

  factory ObsidianSyncPairing.fromJson(Map<String, dynamic> json) =>
      ObsidianSyncPairing(
        deviceId: json['device_id'] as String? ?? '',
        activationCode: json['activation_code'] as String? ?? '',
        expiresAt: json['expires_at'] as String? ?? '',
        replacesPrevious: json['replaces_previous'] as bool? ?? false,
      );

  final String deviceId;
  final String activationCode;
  final String expiresAt;
  final bool replacesPrevious;
}

class TranscriptionBackendStatus {
  const TranscriptionBackendStatus({
    required this.counts,
    required this.jobs,
    this.asr,
    this.customAsr = const CustomAsrConfig(),
    this.customAsrs = const [],
  });

  factory TranscriptionBackendStatus.fromJson(Map<String, dynamic> json) {
    final countsJson = json['counts'] is Map
        ? Map<String, dynamic>.from(json['counts'] as Map)
        : const <String, dynamic>{};
    final asrJson = json['asr'] is Map
        ? Map<String, dynamic>.from(json['asr'] as Map)
        : const <String, dynamic>{};
    final legacyCustom = json['custom_asr'] is Map
        ? CustomAsrConfig.fromJson(
            Map<String, dynamic>.from(json['custom_asr'] as Map),
          )
        : asrJson['custom_asr'] is Map
        ? CustomAsrConfig.fromJson(
            Map<String, dynamic>.from(asrJson['custom_asr'] as Map),
          )
        : const CustomAsrConfig();
    final customAsrsJson = json['custom_asrs'] is List
        ? json['custom_asrs'] as List
        : asrJson['custom_asrs'] is List
        ? asrJson['custom_asrs'] as List
        : const <dynamic>[];
    final customAsrs = customAsrsJson
        .whereType<Map>()
        .map(
          (config) =>
              CustomAsrConfig.fromJson(Map<String, dynamic>.from(config)),
        )
        .where((config) => config.id.isNotEmpty)
        .toList();
    return TranscriptionBackendStatus(
      counts: countsJson.map(
        (key, value) => MapEntry(key, (value as num).toInt()),
      ),
      jobs: (json['jobs'] is List ? json['jobs'] as List : const <dynamic>[])
          .whereType<Map>()
          .map(
            (job) =>
                TranscriptionJobStatus.fromJson(Map<String, dynamic>.from(job)),
          )
          .where((job) => job.recordingName.isNotEmpty)
          .toList(),
      asr: json['asr'] is Map ? TranscriptionAsrStatus.fromJson(asrJson) : null,
      customAsr: customAsrs.isNotEmpty ? customAsrs.first : legacyCustom,
      customAsrs: customAsrs,
    );
  }

  final Map<String, int> counts;
  final List<TranscriptionJobStatus> jobs;
  final TranscriptionAsrStatus? asr;
  final CustomAsrConfig customAsr;
  final List<CustomAsrConfig> customAsrs;
}

class WorkFilterProfile {
  const WorkFilterProfile({
    this.enabled = false,
    this.recordingScene = 'work',
    this.workTypes = const {},
    this.customDescription = '',
    this.excludedContent = '',
  });
  factory WorkFilterProfile.fromJson(Map<String, dynamic> json) =>
      WorkFilterProfile(
        enabled: json['enabled'] as bool? ?? false,
        recordingScene:
            const {'work', 'meeting', 'all'}.contains(json['recording_scene'])
            ? json['recording_scene'] as String
            : 'work',
        workTypes: (json['work_types'] as List<dynamic>? ?? const [])
            .map((e) => e.toString())
            .toSet(),
        customDescription: json['custom_description'] as String? ?? '',
        excludedContent: json['excluded_content'] as String? ?? '',
      );
  final bool enabled;
  final String recordingScene;
  final Set<String> workTypes;
  final String customDescription;
  final String excludedContent;
  Map<String, dynamic> toJson() => {
    'enabled': enabled,
    'optimize_enabled': true,
    'recording_scene': recordingScene,
    'work_types': workTypes.toList(),
    'custom_description': customDescription,
    'excluded_content': excludedContent,
  };
  WorkFilterProfile copyWith({
    bool? enabled,
    String? recordingScene,
    Set<String>? workTypes,
    String? customDescription,
    String? excludedContent,
  }) => WorkFilterProfile(
    enabled: enabled ?? this.enabled,
    recordingScene: recordingScene ?? this.recordingScene,
    workTypes: workTypes ?? this.workTypes,
    customDescription: customDescription ?? this.customDescription,
    excludedContent: excludedContent ?? this.excludedContent,
  );
}

TranscriptionJobStatus? matchWorkflowJob({
  required List<TranscriptionJobStatus> jobs,
  required String recordingName,
  required Set<String> baselineRecordingNames,
  required bool workflowActive,
}) {
  if (recordingName.isNotEmpty) {
    for (final job in jobs.reversed) {
      if (job.recordingName == recordingName) return job;
    }
    for (final job in jobs.reversed) {
      if (job.recordingName.startsWith(recordingName) ||
          recordingName.startsWith(job.recordingName)) {
        return job;
      }
    }
    // A known current file must never inherit an unrelated older job.
    return null;
  }
  if (!workflowActive) return null;
  for (final job in jobs.reversed) {
    if (!baselineRecordingNames.contains(job.recordingName)) return job;
  }
  return null;
}

class UnifiedRelease {
  const UnifiedRelease({
    required this.version,
    required this.firmwareVersion,
    required this.firmwareUrl,
    required this.appVersion,
    required this.appUrl,
    required this.iosAppUrl,
  });

  factory UnifiedRelease.fromJson(Map<String, dynamic> json) {
    final firmware = json['firmware'] as Map<String, dynamic>? ?? const {};
    final app = json['app'] as Map<String, dynamic>? ?? const {};
    return UnifiedRelease(
      version: json['version'] as String? ?? '',
      firmwareVersion: firmware['version'] as String? ?? '',
      firmwareUrl: firmware['url'] as String? ?? '',
      appVersion: app['version'] as String? ?? '',
      appUrl: app['url'] as String? ?? '',
      iosAppUrl: app['ios_url'] as String? ?? '',
    );
  }

  final String version;
  final String firmwareVersion;
  final String firmwareUrl;
  final String appVersion;
  final String appUrl;
  final String iosAppUrl;
}

class BleDeviceStatus {
  const BleDeviceStatus({
    this.deviceId = '',
    this.recording = false,
    this.seconds = 0,
    this.uploading = false,
    this.uploadSentBytes = 0,
    this.uploadTotalBytes = 0,
    this.file = '',
    this.sdFreeMb,
    this.sdTotalMb,
    this.firmware = '',
    this.otaStatus = '',
    this.wifiAddress = '',
    this.cloudConfigured = false,
    this.cloudStatus = '',
    this.cloudConfigRevision = 0,
  });

  factory BleDeviceStatus.parse(String payload) {
    final fields = <String, String>{};
    for (final item in payload.split(';')) {
      final divider = item.indexOf('=');
      if (divider > 0) {
        fields[item.substring(0, divider)] = item.substring(divider + 1);
      }
    }
    return BleDeviceStatus(
      deviceId: fields['device_id'] ?? '',
      recording: fields['recording'] == '1',
      seconds: int.tryParse(fields['seconds'] ?? '') ?? 0,
      uploading: fields['uploading'] == '1',
      uploadSentBytes: int.tryParse(fields['upload_sent'] ?? '') ?? 0,
      uploadTotalBytes: int.tryParse(fields['upload_total'] ?? '') ?? 0,
      file: fields['file'] ?? '',
      sdFreeMb: double.tryParse(fields['sd_free_mb'] ?? ''),
      sdTotalMb: double.tryParse(fields['sd_total_mb'] ?? ''),
      firmware: fields['firmware'] ?? '',
      otaStatus: fields['ota'] ?? '',
      wifiAddress: fields['wifi'] ?? '',
      cloudConfigured: fields['cloud'] == 'ready',
      cloudStatus: fields['cloud_status'] ?? '',
      cloudConfigRevision:
          int.tryParse(fields['cloud_config_revision'] ?? '') ?? 0,
    );
  }

  final String deviceId;
  final bool recording;
  final int seconds;
  final bool uploading;
  final int uploadSentBytes;
  final int uploadTotalBytes;
  final String file;
  final double? sdFreeMb;
  final double? sdTotalMb;
  final String firmware;
  final String otaStatus;
  final String wifiAddress;
  final bool cloudConfigured;
  final String cloudStatus;
  final int cloudConfigRevision;
}

class LanDevice {
  const LanDevice({required this.name, required this.address});

  final String name;
  final String address;
}
