import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

import 'models.dart';
import 'services/ble_recorder.dart';
import 'services/device_api.dart';
import 'services/mdns_discovery.dart';
import 'transcript.dart';

class RecordingBeanController extends ChangeNotifier {
  RecordingBeanController({
    DeviceApi? api,
    BleRecorderService? ble,
    MdnsDiscovery? discovery,
    String? authToken,
  }) : _api = api ?? DeviceApi(),
       _ble = ble ?? BleRecorderService(),
       _discovery = discovery ?? MdnsDiscovery() {
    _api.setAuthToken(authToken);
  }

  static const defaultBackendAddress = 'http://192.168.0.107:8787';
  static const defaultReleaseAddress = 'http://192.168.0.107:8790';
  static const _downloadsChannel = MethodChannel(
    'com.aibean.controller/downloads',
  );

  final DeviceApi _api;
  final BleRecorderService _ble;
  final MdnsDiscovery _discovery;
  final Connectivity _connectivity = Connectivity();
  SharedPreferences? _preferences;
  final List<StreamSubscription<dynamic>> _subscriptions = [];
  Timer? _statusTimer;
  Timer? _backendTimer;
  Timer? _recordingTimer;
  Timer? _scanTimer;
  Timer? _fileTimeout;
  Timer? _reconnectTimer;
  Timer? _wifiDownloadTimer;
  Timer? _connectivityRefreshTimer;
  Timer? _otaInstallTimer;
  BleCandidate? _preferredDevice;
  RecordingFile? _pendingDownload;
  bool _manualDisconnect = false;
  int _reconnectAttempts = 0;
  DateTime? _lastBleStatusAt;
  int _lastBleStatusSeconds = 0;
  DateTime? _lastDownloadNotificationAt;
  int _lastDownloadNotifiedBytes = 0;
  Set<String> _workflowBaselineJobs = {};

  static const _timedWorkflowStatuses = {
    'processing',
    'transcribing',
    'note_pending',
    'note_processing',
    'ai_pending',
    'optimizing',
    'filtering',
  };

  bool initialized = false;
  int pageIndex = 0;
  String address = '';
  bool connected = false;
  bool loading = false;
  bool _recordingCommandInFlight = false;
  DeviceStatus status = const DeviceStatus();
  List<RecordingFile> files = [];
  // 后台重读的收集缓冲：读取期间界面继续显示旧列表，读完原子替换——
  // 避免"刷新先显示暂无录音再闪出列表"（2026-11-20 用户反馈）。
  List<RecordingFile> _freshFiles = [];
  bool _collectingFresh = false;
  bool filesFromBle = false;
  bool selectionMode = false;
  final Set<String> selectedFileNames = {};
  final Set<String> pendingDeletionNames = {};
  final Set<String> _deletionVerificationNames = {};
  bool _verifyingDeletion = false;
  String downloadingFileName = '';
  int downloadReceivedBytes = 0;
  int downloadTotalBytes = -1;
  bool downloadWaitingForWifi = false;
  String message = '连接录音豆后即可查看录音';

  String backendStatusAddress = defaultBackendAddress;
  TranscriptionBackendStatus? backendStatus;
  CustomAsrConfig customAsrConfig = const CustomAsrConfig();
  List<CustomAsrConfig> customAsrConfigs = const [];
  CloudStorageConfig cloudStorageConfig = const CloudStorageConfig();
  bool cloudStorageLoading = false;
  bool cloudStorageSaving = false;
  bool cloudStorageTesting = false;
  String cloudStorageMessage = '';
  String backendStatusMessage = '进度查询不可用，请联网';
  bool backendReachable = false;
  bool backendLoading = false;
  bool backendModelSwitching = false;
  bool customAsrSaving = false;
  ObsidianSyncStatus? obsidianSyncStatus;
  ObsidianSyncPairing? obsidianSyncPairing;
  bool obsidianSyncLoading = false;
  String obsidianSyncMessage = '';
  WorkFilterProfile workFilterProfile = const WorkFilterProfile();
  Map<String, String> workFilterTypes = const {};
  bool workFilterLoading = false;
  String workFilterMessage = '';
  String _pendingBackendModelId = '';
  String _pendingBackendModelName = '';
  TranscriptionAsrModel? _queuedBackendModel;
  final Set<int> aiRetryingJobIds = {};
  String _boundRecorderDeviceId = '';

  void setAuthToken(String? token) => _api.setAuthToken(token);

  bool discovering = false;
  List<LanDevice> discoveredDevices = [];
  bool bleScanning = false;
  bool bleConnecting = false;
  bool bleConnected = false;
  bool bleDisconnecting = false;
  String bleDeviceName = '';
  String pairedDeviceId = '';
  String pairedRecorderDeviceId = '';
  String pairedDeviceName = '';
  List<BleCandidate> bleDevices = [];
  bool bleRecording = false;
  int bleRecordingSeconds = 0;
  bool bleUploading = false;
  int bleUploadSentBytes = 0;
  int bleUploadTotalBytes = 0;
  double? bleSdFreeMb;
  double? bleSdTotalMb;
  String bleCurrentFile = '';
  // 直播流式引擎快照（/live/frame）：录音中每 2.5 秒轮询一次，
  // 实时字幕展示在独立的转写页（transcript_page.dart）。
  bool liveActive = false;
  String livePartial = '';
  List<String> liveSentences = const [];
  String liveText = '';
  Timer? _liveFrameTimer;
  int _liveFrameInflight = 0;

  /// 当前实时数据对应的录音 stem（不含 .wav）。
  /// 「刚录完直接回看」时优先复用这份内存数据，后端重启也不丢。
  String liveTranscriptStem = '';

  /// 历史回看（录音文件页进入）：与实时数据分开存放，避免互相覆盖。
  String historyStem = '';
  List<String> historySentences = const [];
  String historyPartial = '';
  bool historyLoading = false;
  String historyMessage = '';
  int _transcriptRequestId = 0;

  /// 控制页「实时转写」入口卡是否显示的判据。
  bool get hasLiveTranscript =>
      liveSentences.isNotEmpty || livePartial.isNotEmpty;
  String bleFirmware = '';
  String bleOtaStatus = '';
  String bleWifiAddress = '';
  bool bleCloudConfigured = false;
  String bleCloudStatus = '';
  int bleCloudConfigRevision = 0;
  bool bleOtaRequested = false;
  String bleMessage = '未连接录音豆';

  UnifiedRelease? unifiedRelease;
  bool unifiedReleaseChecking = false;
  bool unifiedReleaseUpdateNeeded = false;
  bool unifiedAppUpdateNeeded = false;
  bool unifiedFirmwareUpdateNeeded = false;
  bool unifiedReleaseInstallingApp = false;
  bool unifiedReleaseLastCheckSucceeded = false;
  String unifiedReleaseMessage = '尚未检查更新';

  String workflowFile = '';
  bool workflowWritingToTfCard = false;
  bool workflowUploadStarted = false;
  bool workflowUploadAwaitingConfirmation = false;
  // 本次会话是否真的开始过录音（用户点开始或设备上报在录）。
  // 工作流进度只认这个门卫：刚连上/重连后必须是初始状态，
  // 严禁把后台历史任务误认领成本会话进度（2026-11-24 用户反馈）。
  bool _workflowSessionStarted = false;

  /// 工作流 UI 同样以此为准：会话未开始时任何标志位都不该点亮进度。
  bool get workflowSessionStarted => _workflowSessionStarted;

  String _lastWfSignature = '';

  /// 工作流状态跳变经 /perf 上报到后台日志（监视通道，
  /// HyperOS release 包 logcat 静音，只能走这个渠道远程观测）。
  void _reportWorkflowState(String event) {
    final signature =
        'session=$_workflowSessionStarted rec=$bleRecording '
        'wtc=$workflowWritingToTfCard wus=$workflowUploadStarted '
        'wac=$workflowUploadAwaitingConfirmation file=$workflowFile';
    final changed = signature != _lastWfSignature;
    _lastWfSignature = signature;
    if (!changed && event == 'status') return;
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      unawaited(
        _api.postPerf(base, [
          jsonEncode(
            'WF event=$event $signature jobs=${backendStatus?.jobs.length ?? 0}',
          ),
        ]),
      );
    } catch (_) {}
  }

  TranscriptionJobStatus? get workflowJob => _findWorkflowJob(backendStatus);
  TranscriptionJobStatus? get displayWorkflowJob => workflowJob;

  String get activeDeviceId => '__default__';
  String get accountDeviceId {
    final id = status.deviceId.trim();
    return id.isNotEmpty ? id : pairedRecorderDeviceId.trim();
  }

  String get obsidianSyncDeviceId {
    final connectedDeviceId = status.deviceId.trim();
    if (connectedDeviceId.isNotEmpty) return connectedDeviceId;
    final savedDeviceId = pairedRecorderDeviceId.trim();
    if (savedDeviceId.isNotEmpty) return savedDeviceId;
    final workflowDeviceId = displayWorkflowJob?.deviceId.trim() ?? '';
    if (workflowDeviceId.isNotEmpty && workflowDeviceId != 'legacy') {
      return workflowDeviceId;
    }
    final jobDeviceIds =
        backendStatus?.jobs
            .map((job) => job.deviceId.trim())
            .where((id) => id.isNotEmpty && id != 'legacy')
            .toSet() ??
        const <String>{};
    return jobDeviceIds.length == 1 ? jobDeviceIds.first : '';
  }

  String get accountDeviceName {
    final statusId = status.deviceId.trim();
    final statusName = status.deviceName.trim();
    if (statusId.isNotEmpty && statusName.isNotEmpty) return statusName;
    final connectedName = bleDeviceName.trim();
    if (connectedName.isNotEmpty) return connectedName;
    return pairedDeviceName.trim();
  }

  Future<void> initialize() async {
    _preferences = await SharedPreferences.getInstance();
    address = _preferences?.getString('device_address') ?? '';
    backendStatusAddress =
        _preferences?.getString('backend_status_address') ??
        defaultBackendAddress;
    pairedDeviceId = _preferences?.getString('preferred_ble_device_id') ?? '';
    pairedRecorderDeviceId =
        _preferences?.getString('preferred_recorder_device_id') ?? '';
    pairedDeviceName =
        _preferences?.getString('preferred_ble_device_name') ?? '';
    final localProfile = _preferences?.getString('work_filter_profile');
    if (localProfile != null) {
      try {
        workFilterProfile = WorkFilterProfile.fromJson(
          jsonDecode(localProfile) as Map<String, dynamic>,
        );
      } catch (_) {}
    }
    _listenToBle();
    _subscriptions.add(
      _connectivity.onConnectivityChanged.listen(_onConnectivityChanged),
    );
    _statusTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (connected) refresh(silent: true);
    });
    _backendTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      refreshBackendStatus();
    });
    _recordingTimer = Timer.periodic(
      const Duration(seconds: 1),
      (_) => _tickRecording(),
    );
    initialized = true;
    notifyListeners();
    // Network services are optional for entering the app and for BLE recording.
    // Keep these checks in the background so an unavailable LAN service cannot
    // block the first screen or surface an unhandled startup error.
    unawaited(checkUnifiedRelease());
    unawaited(refreshBackendStatus());
    if (address.isNotEmpty) unawaited(connect(showLoading: false));
  }

  void _listenToBle() {
    _subscriptions.add(
      _ble.candidates.listen((candidate) {
        if (bleConnected || bleConnecting) return;
        final withoutSameDevice = bleDevices.where(
          (item) =>
              item.stableName.isEmpty ||
              item.stableName != candidate.stableName,
        );
        bleDevices = [...withoutSameDevice, candidate]
          ..sort((left, right) {
            final leftPrevious = wasLastConnected(left) ? 0 : 1;
            final rightPrevious = wasLastConnected(right) ? 0 : 1;
            return leftPrevious != rightPrevious
                ? leftPrevious.compareTo(rightPrevious)
                : left.name.compareTo(right.name);
          });
        notifyListeners();
      }),
    );
    _subscriptions.add(_ble.connections.listen(_onBleConnection));
    _subscriptions.add(_ble.statuses.listen(_onBleStatus));
    _subscriptions.add(_ble.fileEvents.listen(_onBleFileEvent));
  }

  void _onConnectivityChanged(List<ConnectivityResult> results) {
    _connectivityRefreshTimer?.cancel();
    if (results.contains(ConnectivityResult.none)) {
      backendReachable = false;
      backendStatusMessage = '进度查询不可用，请联网';
      notifyListeners();
      return;
    }
    backendStatusMessage = '网络已连接，正在刷新转写服务';
    notifyListeners();
    _connectivityRefreshTimer = Timer(const Duration(milliseconds: 300), () {
      unawaited(refreshBackendStatus());
      unawaited(checkUnifiedRelease());
    });
  }

  Future<void> discoverBleDevices() async {
    if (bleScanning || bleConnecting) return;
    if (!await _requestBlePermissions()) {
      bleMessage = '需要附近设备权限才能查找录音豆';
      notifyListeners();
      return;
    }
    bleDevices = [];
    bleScanning = true;
    bleMessage = '正在查找附近录音豆，请选择自己的设备';
    notifyListeners();
    try {
      await _ble.startScan();
      _scanTimer?.cancel();
      _scanTimer = Timer(const Duration(seconds: 6), () async {
        await _ble.stopScan();
        bleScanning = false;
        bleMessage = bleDevices.isEmpty ? '未找到录音豆，请确认设备已开机' : '请选择要连接的录音豆';
        notifyListeners();
      });
    } catch (_) {
      bleScanning = false;
      bleMessage = '蓝牙扫描失败，请检查蓝牙和系统权限';
      notifyListeners();
    }
  }

  Future<bool> _requestBlePermissions() async {
    if (Platform.isAndroid) {
      final results = await [
        Permission.bluetoothScan,
        Permission.bluetoothConnect,
        Permission.locationWhenInUse,
      ].request();
      return results[Permission.bluetoothScan]?.isGranted == true &&
          results[Permission.bluetoothConnect]?.isGranted == true;
    }
    if (Platform.isIOS) {
      final result = await Permission.bluetooth.request();
      return result.isGranted || result.isLimited;
    }
    return true;
  }

  Future<void> connectBleDevice(BleCandidate candidate) async {
    _scanTimer?.cancel();
    _reconnectTimer?.cancel();
    _manualDisconnect = false;
    _reconnectAttempts = 0;
    _preferredDevice = candidate;
    pairedDeviceId = candidate.id;
    pairedDeviceName = candidate.name;
    await _preferences?.setString('preferred_ble_device_id', candidate.id);
    await _preferences?.setString('preferred_ble_device_name', candidate.name);
    bleDevices = [];
    bleScanning = false;
    bleConnecting = true;
    notifyListeners();
    await _ble.connect(candidate);
  }

  bool wasLastConnected(BleCandidate candidate) {
    final savedId = _preferences?.getString('preferred_ble_device_id') ?? '';
    final savedName =
        _preferences?.getString('preferred_ble_device_name') ?? '';
    final stableSaved = savedName
        .replaceFirst('AI Recording Bean ', '')
        .trim()
        .toLowerCase();
    return (stableSaved.isNotEmpty && stableSaved == candidate.stableName) ||
        savedId == candidate.id;
  }

  Future<void> disconnectBleDevice() async {
    if (bleDisconnecting) return;
    bleDisconnecting = true;
    _manualDisconnect = true;
    _preferredDevice = null;
    _reconnectTimer?.cancel();
    notifyListeners();
    try {
      await _ble.disconnect();
    } finally {
      bleConnected = false;
      bleConnecting = false;
      bleDisconnecting = false;
      _resetBleSessionState();
      bleMessage = '蓝牙已断开，可查找并选择录音豆';
      notifyListeners();
    }
  }

  void _onBleConnection(BleConnectionEvent event) {
    bleConnected = event.connected;
    bleConnecting = !event.connected && event.message.startsWith('正在');
    if (event.name.isNotEmpty) bleDeviceName = event.name;
    bleMessage = event.message;
    if (event.connected) {
      // 新会话：把后台已有任务全部纳入基线，工作流回到初始状态——
      // 上次会话的任务不再展示；本次录音产生的新任务不在基线里，
      // 会正常走完工作流（2026-11-20 用户反馈：重连后仍显示上次状态）。
      _workflowBaselineJobs =
          backendStatus?.jobs.map((job) => job.recordingName).toSet() ?? {};
      workflowFile = '';
      workflowWritingToTfCard = false;
      workflowUploadStarted = false;
      workflowUploadAwaitingConfirmation = false;
      _workflowSessionStarted = false;
      _lastWfSignature = '';
      debugPrint('[WF] session reset on connect');
      _reportWorkflowState('reset-connect');
      // 注意：这里刻意不清 live 转写数据——断线/重连后转写页要保留
      // 已收到的内容；真正的清理在每次 START 的 _resetLiveTranscript()。
      _reconnectAttempts = 0;
      _reconnectTimer?.cancel();
      unawaited(
        _ble.send('TIME:${DateTime.now().millisecondsSinceEpoch ~/ 1000}'),
      );
      // 连接性能打点上报（fire-and-forget，仅供后台日志分析）
      if (_ble.lastPerfTrace.isNotEmpty) {
        try {
          final perfBase = _api.normalizeAddress(
            backendStatusAddress,
            statusService: true,
          );
          unawaited(_api.postPerf(perfBase, List.of(_ble.lastPerfTrace)));
        } catch (_) {
          // 上报失败不影响连接
        }
      }
      unawaited(refreshFiles());
      unawaited(checkUnifiedRelease());
    } else if (!event.message.startsWith('正在')) {
      _resetBleSessionState();
      if (!_manualDisconnect && event.message == '蓝牙连接已断开') {
        _scheduleBleReconnect();
      }
    }
    notifyListeners();
  }

  void _scheduleBleReconnect() {
    final candidate = _preferredDevice;
    if (candidate == null || _reconnectAttempts >= 3) return;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 2), () async {
      if (bleConnected || _preferredDevice?.id != candidate.id) return;
      _reconnectAttempts += 1;
      bleConnecting = true;
      bleMessage = '蓝牙已断开，正在重连';
      notifyListeners();
      await _ble.connect(candidate);
    });
  }

  void _onBleStatus(BleDeviceStatus value) {
    final wasRecording = bleRecording;
    final wasUploading = bleUploading;
    final recorderDeviceId = value.deviceId.trim();
    if (_isValidRecorderDeviceId(recorderDeviceId) &&
        recorderDeviceId != pairedRecorderDeviceId) {
      pairedRecorderDeviceId = recorderDeviceId;
      final preferences = _preferences;
      if (preferences != null) {
        unawaited(
          preferences
              .setString('preferred_recorder_device_id', recorderDeviceId)
              .then<void>((_) {}),
        );
      }
      unawaited(_bindRecorderInBackground(recorderDeviceId));
    }
    bleRecording = value.recording;
    bleRecordingSeconds = value.seconds;
    _syncLiveFramePolling();
    bleUploading = value.uploading;
    bleUploadSentBytes = value.uploadSentBytes;
    bleUploadTotalBytes = value.uploadTotalBytes;
    bleSdFreeMb = value.sdFreeMb;
    bleSdTotalMb = value.sdTotalMb;
    if (value.file.isNotEmpty) {
      bleCurrentFile = value.file;
    } else if (wasUploading && !value.uploading) {
      bleCurrentFile = '';
    }
    bleFirmware = value.firmware;
    bleOtaStatus = value.otaStatus;
    bleWifiAddress = value.wifiAddress;
    bleCloudConfigured = value.cloudConfigured;
    bleCloudStatus = value.cloudStatus;
    bleCloudConfigRevision = value.cloudConfigRevision;
    _lastBleStatusAt = DateTime.now();
    _lastBleStatusSeconds = value.seconds;

    if (bleOtaRequested) {
      final ota = value.otaStatus.trim();
      final target = unifiedRelease?.firmwareVersion ?? '';
      final terminalFailure =
          ota.startsWith('update_failed') ||
          ota == 'network_failed' ||
          ota == 'manifest_failed' ||
          ota == 'manifest_invalid' ||
          ota == 'not_configured' ||
          ota == 'busy';
      final reachedTarget =
          target.isNotEmpty && !_versionNewer(target, value.firmware);
      if (terminalFailure || ota == 'up_to_date' || reachedTarget) {
        _otaInstallTimer?.cancel();
        _otaInstallTimer = null;
        bleOtaRequested = false;
        if (terminalFailure) {
          unifiedReleaseMessage = '固件更新失败：${_formatOtaStatus(ota)}';
        } else if (reachedTarget) {
          unifiedReleaseMessage = '固件更新完成';
        }
      }
    }

    if (_workflowSessionStarted &&
        value.file.isNotEmpty &&
        // 本会话文件一经确定就锁定：设备端幽灵上传（旧固件 bug）会短暂
        // 上报别的文件名（如历史分段），绝不允许覆盖本会话的录音名。
        (workflowFile.isEmpty ||
            value.file == workflowFile ||
            value.file.startsWith(workflowFile)) &&
        (value.recording ||
            value.uploading ||
            wasRecording ||
            wasUploading ||
            workflowWritingToTfCard ||
            workflowUploadStarted ||
            workflowUploadAwaitingConfirmation)) {
      workflowFile = value.file;
    }
    if (_workflowSessionStarted && wasRecording && !value.recording) {
      workflowWritingToTfCard = true;
    }
    if (value.recording) {
      // 设备在录即视为本会话已开始（覆盖重连后直接看到在录的场景）。
      if (!_workflowSessionStarted) {
        debugPrint('[WF] session start: device reports recording');
        _reportWorkflowState('start-device-recording');
      }
      _workflowSessionStarted = true;
      workflowWritingToTfCard = false;
      workflowUploadStarted = false;
      workflowUploadAwaitingConfirmation = false;
    }
    if (value.uploading && _workflowSessionStarted) {
      workflowWritingToTfCard = false;
      workflowUploadStarted = true;
      workflowUploadAwaitingConfirmation = false;
    } else if (wasUploading &&
        !value.uploading &&
        _workflowSessionStarted &&
        !value.recording) {
      workflowUploadStarted = true;
      workflowUploadAwaitingConfirmation = true;
      unawaited(refreshBackendStatus());
    }
    _reportWorkflowState('status');
    if (_pendingDownload != null && _validIpv4(value.wifiAddress)) {
      final pending = _pendingDownload!;
      _pendingDownload = null;
      _wifiDownloadTimer?.cancel();
      unawaited(
        _downloadFrom(pending, Uri.parse('http://${value.wifiAddress}')),
      );
    }
    notifyListeners();
  }

  bool _isValidRecorderDeviceId(String value) =>
      value.isNotEmpty && RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(value);

  void _tickRecording() {
    var shouldNotify = false;
    if (bleConnected && bleRecording && _lastBleStatusAt != null) {
      final estimate =
          _lastBleStatusSeconds +
          DateTime.now().difference(_lastBleStatusAt!).inSeconds;
      if (estimate > bleRecordingSeconds) {
        bleRecordingSeconds = estimate;
        shouldNotify = true;
      }
    }
    final job = displayWorkflowJob;
    if (job != null &&
        job.stageStartedAt.isNotEmpty &&
        _timedWorkflowStatuses.contains(job.status)) {
      shouldNotify = true;
    }
    if (shouldNotify) notifyListeners();
  }

  Future<void> toggleRecording() async {
    // 连点防抖（2026-11-20 审计）：命令在途时忽略重复点击，避免双击
    // 发两次 START/STOP 造成设备状态错乱。
    if (_recordingCommandInFlight) return;
    if (!bleConnected) {
      bleMessage = '请先连接录音豆';
      notifyListeners();
      return;
    }
    final command = bleRecording
        ? 'STOP'
        : 'START:${DateTime.now().millisecondsSinceEpoch ~/ 1000}';
    _recordingCommandInFlight = true;
    try {
      await _sendRecordingCommand(command);
    } finally {
      _recordingCommandInFlight = false;
      notifyListeners();
    }
  }

  Future<void> _sendRecordingCommand(String command) async {
    final sent = await _ble.send(command);
    if (!sent) {
      bleConnected = false;
      _resetBleSessionState();
      bleMessage = '蓝牙命令发送失败，请重新连接';
    } else {
      bleMessage = command == 'STOP' ? '已发送停止录音命令' : '已发送开始录音命令';
      if (command == 'STOP') {
        bleRecording = false;
        workflowWritingToTfCard = true;
        // 立即停轮询，不等下一次 BLE 状态上报
        _syncLiveFramePolling();
      }
      if (command.startsWith('START')) {
        // 新会话：清掉上一段录音的残留句子，避免混入新转写
        _resetLiveTranscript();
        bleRecording = true;
        bleRecordingSeconds = 0;
        bleCurrentFile = '';
        _lastBleStatusSeconds = 0;
        _lastBleStatusAt = DateTime.now();
        _workflowSessionStarted = true;
        debugPrint('[WF] session start: user pressed record');
        _reportWorkflowState('start-user-record');
        _workflowBaselineJobs =
            backendStatus?.jobs.map((job) => job.recordingName).toSet() ?? {};
        workflowFile = '';
        workflowWritingToTfCard = false;
        workflowUploadStarted = false;
        workflowUploadAwaitingConfirmation = false;
      }
    }
    notifyListeners();
  }

  void _resetBleSessionState() {
    bleRecording = false;
    bleRecordingSeconds = 0;
    bleUploading = false;
    bleUploadSentBytes = 0;
    bleUploadTotalBytes = 0;
    bleSdFreeMb = null;
    bleSdTotalMb = null;
    bleCurrentFile = '';
    bleFirmware = '';
    bleOtaStatus = '';
    bleOtaRequested = false;
    bleWifiAddress = '';
    bleCloudConfigured = false;
    bleCloudStatus = '';
    bleCloudConfigRevision = 0;
    _lastBleStatusAt = null;
    _lastBleStatusSeconds = 0;
    workflowFile = '';
    workflowWritingToTfCard = false;
    workflowUploadStarted = false;
    workflowUploadAwaitingConfirmation = false;
    _workflowSessionStarted = false;
    debugPrint('[WF] session reset on disconnect/ble-reset');
    _lastWfSignature = '';
    _reportWorkflowState('reset-disconnect');
    _workflowBaselineJobs = {};
    if (!connected) _clearCachedDeviceFiles();
  }

  void _clearCachedDeviceFiles() {
    files = [];
    filesFromBle = false;
    selectionMode = false;
    selectedFileNames.clear();
    pendingDeletionNames.clear();
    _deletionVerificationNames.clear();
    _verifyingDeletion = false;
  }

  Future<void> refreshFiles() async {
    if (downloadingFileName.isNotEmpty) return;
    if (bleConnected) {
      final sent = await _ble.requestFiles();
      if (!sent) {
        message = '蓝牙文件读取命令发送失败，请重新连接';
        notifyListeners();
        return;
      }
      // 不清空现有列表：读取期间旧列表继续显示，读完原子替换。
      _freshFiles = [];
      _collectingFresh = true;
      filesFromBle = true;
      selectionMode = false;
      selectedFileNames.clear();
      loading = true;
      message = '正在读取录音';
      _startFileTimeout('读取');
      notifyListeners();
      return;
    }
    if (address.isEmpty) {
      message = '请先连接录音豆';
      notifyListeners();
      return;
    }
    try {
      final result = await _api.files(_api.normalizeAddress(address));
      files = result
          .where((file) => !pendingDeletionNames.contains(file.name))
          .toList();
      filesFromBle = false;
      message = '已读取设备 TF 卡中的 ${files.length} 个录音';
    } catch (error) {
      message = '读取录音列表失败：${_userMessage(error)}';
    }
    notifyListeners();
  }

  void _onBleFileEvent(BleFileEvent event) {
    switch (event) {
      case BleFileFound(:final file):
        if (_collectingFresh && !_freshFiles.any((item) => item.name == file.name)) {
          _freshFiles = [..._freshFiles, file];
        }
        if (!files.any((item) => item.name == file.name)) {
          files = [...files, file];
        }
      case BleFilePageFinished(:final nextCursor, :final hasMore):
        if (hasMore) {
          unawaited(_ble.requestFiles(nextCursor));
        } else {
          _fileTimeout?.cancel();
          loading = false;
          // 读取完成：一次性替换为设备上的最新列表（旧列表中的
          // 失效条目随之移除），替换前界面始终有内容显示。
          if (_collectingFresh) {
            files = _freshFiles;
            _freshFiles = [];
            _collectingFresh = false;
          }
          if (_verifyingDeletion) {
            final remaining = files
                .where((file) => _deletionVerificationNames.contains(file.name))
                .length;
            message = remaining == 0
                ? '已确认从 TF 卡删除录音'
                : '有 $remaining 个录音未能删除，请重试';
            _deletionVerificationNames.clear();
            _verifyingDeletion = false;
          } else {
            message = '已找到 ${files.length} 个录音';
          }
        }
      case BleFileDeleted(:final name):
        pendingDeletionNames.remove(name);
        _verifyDeletionWhenReady();
      case BleFileFailure(:final operation, :final name, :final message):
        if (operation == 'delete') {
          pendingDeletionNames.remove(name);
          this.message = '设备未能删除 $name';
          _verifyDeletionWhenReady();
        } else {
          _fileTimeout?.cancel();
          loading = false;
          // 读取失败：丢弃未完成的收集，保留原列表显示。
          _collectingFresh = false;
          _freshFiles = [];
          this.message = '蓝牙读取失败：$message';
        }
    }
    notifyListeners();
  }

  void _startFileTimeout(String operation) {
    _fileTimeout?.cancel();
    _fileTimeout = Timer(const Duration(seconds: 20), () {
      pendingDeletionNames.clear();
      // 超时：丢弃未完成的收集，保留原列表显示。
      _collectingFresh = false;
      _freshFiles = [];
      if (operation == '删除' && _deletionVerificationNames.isNotEmpty) {
        _verifyingDeletion = true;
        message = '正在确认删除结果';
        notifyListeners();
        unawaited(refreshFiles());
        return;
      }
      loading = false;
      message = '蓝牙$operation超时，请稍后重试';
      notifyListeners();
    });
  }

  Future<void> deleteFiles(Iterable<RecordingFile> targets) async {
    final list = targets.toList();
    if (list.isEmpty) return;
    selectionMode = false;
    selectedFileNames.clear();
    _deletionVerificationNames
      ..clear()
      ..addAll(list.map((file) => file.name));
    _verifyingDeletion = false;
    pendingDeletionNames.addAll(list.map((file) => file.name));
    loading = true;
    message = '正在从 TF 卡删除录音';
    notifyListeners();
    if (filesFromBle && bleConnected) {
      _startFileTimeout('删除');
      for (final file in list) {
        if (!await _ble.deleteFile(file.name)) {
          pendingDeletionNames.remove(file.name);
        }
      }
      _verifyDeletionWhenReady();
      return;
    }
    try {
      final base = _api.normalizeAddress(address);
      for (final file in list) {
        await _api.deleteFile(base, file.name);
        pendingDeletionNames.remove(file.name);
      }
      files = files
          .where((file) => !_deletionVerificationNames.contains(file.name))
          .toList();
      message = '已从设备 TF 卡删除 ${list.length} 个录音';
    } catch (error) {
      message = '删除失败：${_userMessage(error)}';
      await refreshFiles();
    } finally {
      pendingDeletionNames.clear();
      _deletionVerificationNames.clear();
      _verifyingDeletion = false;
      loading = false;
      notifyListeners();
    }
  }

  void _verifyDeletionWhenReady() {
    if (pendingDeletionNames.isNotEmpty || _verifyingDeletion) return;
    _fileTimeout?.cancel();
    _verifyingDeletion = true;
    message = '正在确认删除结果';
    notifyListeners();
    unawaited(refreshFiles());
  }

  Future<void> downloadFile(RecordingFile file) async {
    if (downloadingFileName.isNotEmpty) {
      message = '正在下载 $downloadingFileName，请等待完成';
      notifyListeners();
      return;
    }
    if (filesFromBle) {
      if (!bleConnected) {
        message = '请先连接蓝牙录音豆后下载';
      } else if (_validIpv4(bleWifiAddress)) {
        await _downloadFrom(file, Uri.parse('http://$bleWifiAddress'));
      } else if (await _ble.requestWifiAccess()) {
        _pendingDownload = file;
        _beginDownload(file.name, waitingForWifi: true);
        message = '正在准备下载 ${file.name}';
        _wifiDownloadTimer?.cancel();
        _wifiDownloadTimer = Timer(const Duration(seconds: 45), () {
          if (_pendingDownload?.name != file.name) return;
          _pendingDownload = null;
          _finishDownloadState();
          message = '设备暂时无法下载，请确认手机和录音豆连接同一 Wi-Fi 后重试';
          notifyListeners();
        });
      } else {
        message = '无法唤醒设备 Wi-Fi，请重新连接蓝牙';
      }
      notifyListeners();
      return;
    }
    if (!connected || address.isEmpty) {
      message = '下载需连接设备 Wi-Fi';
      notifyListeners();
      return;
    }
    await _downloadFrom(file, _api.normalizeAddress(address));
  }

  Future<void> _downloadFrom(RecordingFile file, Uri base) async {
    _beginDownload(file.name);
    message = '正在下载 ${file.name}';
    notifyListeners();
    File? temporaryFile;
    try {
      final destination = await _downloadDestination(file.name);
      if (Platform.isAndroid) temporaryFile = destination;
      await _api.downloadFile(
        base,
        file.name,
        destination,
        onProgress: _onDownloadProgress,
      );
      message = await _saveDownloadedFile(destination, file.name);
    } catch (error) {
      message = '下载失败：${_userMessage(error)}';
    } finally {
      _finishDownloadState();
      if (temporaryFile != null && await temporaryFile.exists()) {
        await temporaryFile.delete();
      }
      notifyListeners();
    }
  }

  void _beginDownload(String name, {bool waitingForWifi = false}) {
    downloadingFileName = name;
    downloadReceivedBytes = 0;
    downloadTotalBytes = -1;
    downloadWaitingForWifi = waitingForWifi;
    _lastDownloadNotificationAt = null;
    _lastDownloadNotifiedBytes = 0;
  }

  void _onDownloadProgress(int receivedBytes, int totalBytes) {
    downloadWaitingForWifi = false;
    downloadReceivedBytes = receivedBytes;
    downloadTotalBytes = totalBytes;
    final now = DateTime.now();
    final shouldNotify =
        receivedBytes == totalBytes ||
        receivedBytes - _lastDownloadNotifiedBytes >= 64 * 1024 ||
        _lastDownloadNotificationAt == null ||
        now.difference(_lastDownloadNotificationAt!) >=
            const Duration(milliseconds: 120);
    if (!shouldNotify) return;
    _lastDownloadNotificationAt = now;
    _lastDownloadNotifiedBytes = receivedBytes;
    notifyListeners();
  }

  void _finishDownloadState() {
    downloadingFileName = '';
    downloadReceivedBytes = 0;
    downloadTotalBytes = -1;
    downloadWaitingForWifi = false;
    _lastDownloadNotificationAt = null;
    _lastDownloadNotifiedBytes = 0;
  }

  Future<File> _downloadDestination(String name) async {
    if (Platform.isAndroid) {
      final temporary = await getTemporaryDirectory();
      return File('${temporary.path}/$name');
    }
    final root = Platform.isIOS
        ? await getApplicationDocumentsDirectory()
        : (await getDownloadsDirectory()) ??
              await getApplicationDocumentsDirectory();
    final folder = Platform.isIOS
        ? root
        : await Directory('${root.path}/智能录音豆').create(recursive: true);
    return File('${folder.path}/$name');
  }

  Future<String> _saveDownloadedFile(File source, String name) async {
    if (Platform.isAndroid) {
      try {
        await _saveToAndroidDownloads(source, name);
      } on PlatformException catch (error) {
        if (error.code != 'storage_permission_required') rethrow;
        final permission = await Permission.storage.request();
        if (!permission.isGranted) {
          throw const FileSystemException('需要存储权限才能保存到下载目录');
        }
        await _saveToAndroidDownloads(source, name);
      }
      return '已保存到“下载/智能录音豆”';
    }
    if (Platform.isIOS) {
      await SharePlus.instance.share(
        ShareParams(
          files: [XFile(source.path, mimeType: 'audio/wav')],
          subject: name,
        ),
      );
      return '已打开系统面板，可播放或存储录音';
    }
    return '已保存到“下载/智能录音豆”';
  }

  Future<void> _saveToAndroidDownloads(File source, String name) async {
    await _downloadsChannel.invokeMethod<void>('saveAudio', {
      'sourcePath': source.path,
      'fileName': name,
    });
  }

  bool _validIpv4(String value) {
    final parts = value.trim().split('.');
    return parts.length == 4 &&
        parts.every((part) {
          final number = int.tryParse(part);
          return number != null && number >= 0 && number <= 255;
        });
  }

  Future<void> connect({bool showLoading = true}) async {
    if (address.trim().isEmpty) return;
    if (showLoading) {
      loading = true;
      message = '正在连接设备';
      notifyListeners();
    }
    try {
      final normalized = _api.normalizeAddress(address);
      final current = await _api.status(normalized);
      await _api.setClock(normalized);
      address = normalized.toString();
      status = current;
      connected = true;
      loading = false;
      message = '已连接，设备时间已同步';
      await _preferences?.setString('device_address', address);
      notifyListeners();
      await refreshFiles();
    } catch (error) {
      connected = false;
      loading = false;
      if (!bleConnected) _clearCachedDeviceFiles();
      message = bleConnected ? '蓝牙已连接' : _userMessage(error);
      notifyListeners();
    }
  }

  Future<void> refresh({bool silent = false}) async {
    if (address.isEmpty) return;
    try {
      status = await _api.status(_api.normalizeAddress(address));
      connected = true;
      message = status.recording
          ? '正在录音，停止后将自动转写'
          : status.uploadInProgress
          ? '正在处理录音'
          : '录音会自动转写并生成笔记';
    } catch (_) {
      if (!silent) message = '设备不可达，正在等待重连';
      connected = false;
      if (!bleConnected) _clearCachedDeviceFiles();
    }
    notifyListeners();
  }

  Future<void> refreshControlPage() async {
    _connectivityRefreshTimer?.cancel();
    backendStatusMessage = '正在刷新转写服务';
    notifyListeners();

    final deadline = DateTime.now().add(const Duration(seconds: 15));
    while ((backendLoading ||
            backendModelSwitching ||
            unifiedReleaseChecking) &&
        DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }

    final refreshes = <Future<void>>[
      refreshBackendStatus(),
      checkUnifiedRelease(),
    ];
    if (bleConnected) {
      refreshes.add(_ble.requestStatus().then((_) {}));
    }
    if (connected && address.isNotEmpty) {
      refreshes.add(refresh(silent: true));
    }
    try {
      await Future.wait(refreshes);
    } catch (_) {
      // A failed optional refresh must not make the control screen unusable.
      backendStatusMessage = '部分服务暂不可用，蓝牙录音仍可继续';
      notifyListeners();
    }
  }

  void updateAddress(String value) {
    address = value;
    notifyListeners();
  }

  Future<void> discoverLanDevices() async {
    if (discovering) return;
    discovering = true;
    discoveredDevices = [];
    message = '正在查找局域网设备';
    notifyListeners();
    try {
      discoveredDevices = await _discovery.discover();
      message = discoveredDevices.isEmpty ? '未找到局域网设备' : '已找到局域网设备';
    } catch (_) {
      message = '未找到局域网设备';
    } finally {
      discovering = false;
      notifyListeners();
    }
  }

  Future<void> useLanDevice(LanDevice device) async {
    address = device.address;
    discoveredDevices = [];
    await connect();
  }

  Future<void> refreshBackendStatus() async {
    if (backendLoading ||
        backendModelSwitching ||
        backendStatusAddress.isEmpty) {
      return;
    }
    final connectivity = await _connectivity.checkConnectivity();
    if (connectivity.contains(ConnectivityResult.none)) {
      backendReachable = false;
      backendStatusMessage = '进度查询不可用，请联网';
      notifyListeners();
      return;
    }
    backendLoading = true;
    Uri? normalized;
    try {
      normalized = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final refreshedStatus = await _api.transcriptionStatus(normalized);
      backendStatus = refreshedStatus;
      customAsrConfig = refreshedStatus.customAsr;
      customAsrConfigs = refreshedStatus.customAsrs;
      // 只在本会话真的开始过录音时才认领任务进度；
      // 刚连上时后台的历史任务一律不进工作流（2026-11-24 用户反馈）。
      final job = _workflowSessionStarted
          ? _findWorkflowJob(refreshedStatus)
          : null;
      if (job != null) {
        debugPrint(
          '[WF] adopt job ${job.recordingName} status=${job.status} '
          'session=$_workflowSessionStarted',
        );
        _reportWorkflowState('adopt-job status=${job.status}');
        workflowFile = job.recordingName;
        workflowWritingToTfCard = false;
        workflowUploadStarted = true;
        workflowUploadAwaitingConfirmation = false;
      }
      backendStatusAddress = normalized.toString();
      backendReachable = true;
      unawaited(_syncWorkFilterSilently());
      _updateBackendModelMessage(refreshedStatus.asr);
      await _preferences?.setString(
        'backend_status_address',
        backendStatusAddress,
      );
    } catch (error) {
      backendReachable = false;
      final uri = normalized;
      var target = '';
      if (uri != null) {
        final omitPort = (uri.scheme == 'http' && uri.port == 80) ||
            (uri.scheme == 'https' && uri.port == 443);
        target = '${uri.host}${omitPort ? '' : ':${uri.port}'}';
      }
      backendStatusMessage =
          '无法连接转写服务${target.isEmpty ? '' : '（$target）'}'
          '，${_backendFailureReason(error)}';
    } finally {
      backendLoading = false;
      notifyListeners();
      final queuedModel = _queuedBackendModel;
      _queuedBackendModel = null;
      if (queuedModel != null) unawaited(selectBackendModel(queuedModel));
    }
  }

  Future<void> selectBackendModel(TranscriptionAsrModel model) async {
    if (!model.configured || backendModelSwitching || !backendReachable) {
      return;
    }
    if (backendLoading) {
      _queuedBackendModel = model;
      return;
    }
    backendModelSwitching = true;
    _pendingBackendModelId = model.id;
    _pendingBackendModelName = model.name;
    backendStatusMessage = '正在切换到 ${model.name}';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final asr = await _api.selectModel(base, model.id);
      await _applySelectedModel(asr);
      backendReachable = true;
      _confirmPendingBackendModel(asr);
    } catch (_) {
      try {
        final base = _api.normalizeAddress(
          backendStatusAddress,
          statusService: true,
        );
        final confirmed = await _api.transcriptionStatus(base);
        backendStatus = confirmed;
        backendReachable = true;
        if (confirmed.asr?.selectedModelId == model.id) {
          await _preferences?.setString('transcription_model', model.id);
          _confirmPendingBackendModel(confirmed.asr);
        } else {
          backendStatusMessage = '切换请求已发送，正在确认 ${model.name}';
        }
      } catch (_) {
        backendStatusMessage = '切换请求已发送，正在自动确认';
      }
    } finally {
      backendModelSwitching = false;
      notifyListeners();
    }
  }

  Future<void> saveCustomAsr(CustomAsrConfig config) async {
    if (backendStatusAddress.isEmpty || customAsrSaving) return;
    customAsrSaving = true;
    backendStatusMessage = '正在保存自定义 ASR服务';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final result = await _api.saveCustomAsr(base, config);
      final saved = result['custom_asr'];
      if (saved is Map<String, dynamic>) {
        customAsrConfig = CustomAsrConfig.fromJson(saved);
      }
      await refreshBackendStatus();
      backendStatusMessage = config.enabled
          ? '已保存并使用 ${config.name}'
          : '已保存 ${config.name}（已停用）';
    } catch (error) {
      backendStatusMessage = '保存自定义 ASR服务失败：${_userMessage(error)}';
      rethrow;
    } finally {
      customAsrSaving = false;
      notifyListeners();
    }
  }

  Future<void> deleteCustomAsr(CustomAsrConfig config) async {
    if (config.id.isEmpty || backendStatusAddress.isEmpty || customAsrSaving) {
      return;
    }
    customAsrSaving = true;
    backendStatusMessage = '正在删除 ${config.name}';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      await _api.deleteCustomAsr(base, config.id);
      await refreshBackendStatus();
      backendStatusMessage = '已删除 ${config.name}';
    } catch (error) {
      backendStatusMessage = '删除自定义 ASR服务失败：${_userMessage(error)}';
      rethrow;
    } finally {
      customAsrSaving = false;
      notifyListeners();
    }
  }

  Future<void> loadCloudStorage() async {
    if (cloudStorageLoading || backendStatusAddress.isEmpty) return;
    cloudStorageLoading = true;
    cloudStorageMessage = '正在读取云端存储配置';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      cloudStorageConfig = await _api.cloudStorage(base);
      cloudStorageMessage = '';
    } catch (error) {
      cloudStorageMessage = '读取云端存储配置失败：${_userMessage(error)}';
    } finally {
      cloudStorageLoading = false;
      notifyListeners();
    }
  }

  Future<void> testCloudStorage(CloudStorageConfig config) async {
    if (cloudStorageTesting || backendStatusAddress.isEmpty) return;
    cloudStorageTesting = true;
    cloudStorageMessage = config.usesBackendDefault
        ? '正在测试后端默认存储'
        : '正在测试云端存储连接';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final result = await _api.testCloudStorage(base, config);
      final storage = result['storage'];
      if (storage is Map<String, dynamic>) {
        cloudStorageConfig = CloudStorageConfig.fromJson(storage);
      }
      cloudStorageMessage =
          '连接成功，已读取 ${(result['recordings_count'] as num?)?.toInt() ?? 0} 个录音、'
          '${(result['notes_count'] as num?)?.toInt() ?? 0} 篇笔记';
    } catch (error) {
      cloudStorageMessage = '连接测试失败：${_userMessage(error)}';
      rethrow;
    } finally {
      cloudStorageTesting = false;
      notifyListeners();
    }
  }

  Future<void> saveCloudStorage(
    CloudStorageConfig config, {
    bool syncRecorder = false,
  }) async {
    if (cloudStorageSaving || backendStatusAddress.isEmpty) return;
    cloudStorageSaving = true;
    cloudStorageMessage = '正在保存云端存储配置';
    notifyListeners();
    var backendSaved = false;
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final result = await _api.saveCloudStorage(base, config);
      backendSaved = true;
      final storage = result['storage'];
      if (storage is Map<String, dynamic>) {
        cloudStorageConfig = CloudStorageConfig.fromJson(storage);
      } else {
        cloudStorageConfig = config;
      }
      if (config.usesBackendDefault) {
        cloudStorageMessage = '已使用后端默认 MinIO，密钥仅保存在后端';
      } else if (syncRecorder && config.isS3) {
        await _syncCloudStorageToRecorder(config);
        cloudStorageMessage = '后端已保存，云端存储已同步到录音豆';
      } else if (config.isS3 && !bleConnected) {
        cloudStorageMessage = '后端已保存，连接录音豆后可同步设备';
      } else if (!config.isS3) {
        cloudStorageMessage = '后端自定义存储适配器已保存';
      } else {
        cloudStorageMessage = '云端存储配置已保存';
      }
    } catch (error) {
      cloudStorageMessage = backendSaved
          ? '后端已保存，但录音豆同步失败：${_userMessage(error)}'
          : '保存云端存储配置失败：${_userMessage(error)}';
      rethrow;
    } finally {
      cloudStorageSaving = false;
      notifyListeners();
    }
  }

  Future<void> syncCloudStorageToRecorder() async {
    if (cloudStorageConfig.usesBackendDefault) {
      cloudStorageMessage = '后端默认存储不会向手机或录音豆下发密钥';
      notifyListeners();
      return;
    }
    if (!cloudStorageConfig.isS3) {
      cloudStorageMessage = '自定义适配器不直接下发到录音豆';
      notifyListeners();
      return;
    }
    cloudStorageSaving = true;
    cloudStorageMessage = '正在同步云端存储到录音豆';
    notifyListeners();
    try {
      await _syncCloudStorageToRecorder(cloudStorageConfig);
      cloudStorageMessage = '云端存储已同步到录音豆';
    } catch (error) {
      cloudStorageMessage = '同步录音豆失败：${_userMessage(error)}';
      rethrow;
    } finally {
      cloudStorageSaving = false;
      notifyListeners();
    }
  }

  Future<void> _syncCloudStorageToRecorder(CloudStorageConfig config) async {
    if (!bleConnected) {
      throw StateError('请先连接录音豆');
    }
    if (config.usesBackendDefault) {
      throw StateError('后端默认存储不向录音豆下发密钥');
    }
    if (config.endpoint.trim().isEmpty ||
        config.bucket.trim().isEmpty ||
        config.region.trim().isEmpty) {
      throw StateError('录音豆云端存储配置不完整');
    }
    if (config.accessKey.trim().isEmpty || config.secretKey.trim().isEmpty) {
      throw StateError('同步自定义存储到录音豆前，请重新填写 Access Key 和 Secret Key');
    }
    final sent = await _ble.sendStorageConfig(config.toRecorderJson());
    if (!sent) {
      throw StateError('录音豆没有接受云端存储配置');
    }
    await Future<void>.delayed(const Duration(milliseconds: 400));
    if (bleCloudStatus == 'invalid' || bleCloudStatus == 'save_failed') {
      throw StateError('录音豆拒绝了云端存储配置');
    }
  }

  Future<void> loadObsidianSync() async {
    if (obsidianSyncLoading || backendStatusAddress.isEmpty) return;
    obsidianSyncLoading = true;
    obsidianSyncMessage = '正在读取同步状态';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final knownDeviceId = obsidianSyncDeviceId;
      if (knownDeviceId.isNotEmpty) {
        await _bindRecorder(base, knownDeviceId);
      }
      obsidianSyncStatus = await _api.obsidianSyncStatus(base);
      obsidianSyncMessage = obsidianSyncStatus!.bound
          ? ''
          : '首次连接录音豆后会自动完成账户绑定。';
    } catch (error) {
      obsidianSyncMessage = '无法读取同步状态：${_userMessage(error)}';
    } finally {
      obsidianSyncLoading = false;
      notifyListeners();
    }
  }

  Future<void> createObsidianSyncPairing(String targetName) async {
    if (obsidianSyncLoading || backendStatusAddress.isEmpty) return;
    if (obsidianSyncStatus?.bound != true) {
      await loadObsidianSync();
      if (obsidianSyncStatus?.bound != true) return;
    }
    obsidianSyncLoading = true;
    obsidianSyncMessage = '正在生成新电脑的激活码';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      obsidianSyncPairing = await _api.createObsidianSyncPairing(
        base,
        targetName: targetName.trim(),
      );
      obsidianSyncMessage = '';
    } catch (error) {
      obsidianSyncMessage = '生成激活码失败：${_userMessage(error)}';
      rethrow;
    } finally {
      obsidianSyncLoading = false;
      notifyListeners();
    }
  }

  Future<void> _bindRecorderInBackground(String deviceId) async {
    if (backendStatusAddress.isEmpty || _boundRecorderDeviceId == deviceId) {
      return;
    }
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      await _bindRecorder(base, deviceId);
    } catch (_) {}
  }

  Future<void> _bindRecorder(Uri base, String deviceId) async {
    if (_boundRecorderDeviceId == deviceId) return;
    await _api.bindRecorder(base, deviceId);
    _boundRecorderDeviceId = deviceId;
  }

  String get obsidianSyncPairingInfo {
    final pairing = obsidianSyncPairing;
    if (pairing == null || pairing.activationCode.isEmpty) return '';
    try {
      final endpoint = _api
          .normalizeAddress(backendStatusAddress, statusService: true)
          .toString();
      return Uri(
        scheme: 'recording-bean',
        host: 'obsidian-sync',
        queryParameters: {
          'endpoint': endpoint,
          'api_prefix': 'api/sync',
          'activation_code': pairing.activationCode,
        },
      ).toString();
    } catch (_) {
      return '';
    }
  }

  Future<void> loadWorkFilter() async {
    const id = '__default__';
    if (backendStatusAddress.isEmpty) return;
    workFilterLoading = true;
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final data = await _api.workFilter(base, id);
      workFilterTypes = (data['types'] as Map<String, dynamic>? ?? const {})
          .map((k, v) => MapEntry(k, v.toString()));
      if (_preferences?.getString('work_filter_profile') == null) {
        workFilterProfile = WorkFilterProfile.fromJson(
          data['profile'] as Map<String, dynamic>? ?? const {},
        );
      }
      workFilterMessage = '';
    } catch (_) {
      workFilterMessage = '当前使用手机中保存的设置';
    } finally {
      workFilterLoading = false;
      notifyListeners();
    }
  }

  Future<void> saveWorkFilter(WorkFilterProfile profile) async {
    if (profile.enabled &&
        profile.recordingScene == 'work' &&
        profile.workTypes.isEmpty &&
        profile.customDescription.trim().isEmpty) {
      workFilterMessage = '普通工作内容模式下，请选择工作类型或填写工作内容';
      notifyListeners();
      return;
    }
    workFilterProfile = profile;
    await _preferences?.setString(
      'work_filter_profile',
      jsonEncode(profile.toJson()),
    );
    workFilterLoading = true;
    notifyListeners();
    try {
      await _syncWorkFilter();
      workFilterMessage = '筛选设置已保存';
    } catch (_) {
      workFilterMessage = '已保存到手机，连接电脑网络后自动同步';
    } finally {
      workFilterLoading = false;
      notifyListeners();
    }
  }

  Future<void> retryAiProcessing(TranscriptionJobStatus job) async {
    if (job.jobId <= 0 || aiRetryingJobIds.contains(job.jobId)) return;
    aiRetryingJobIds.add(job.jobId);
    backendStatusMessage = '正在提交AI处理重试';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      await _api.retryAiProcessing(base, job.jobId);
      backendStatusMessage = '已提交AI处理重试';
      await refreshBackendStatus();
    } catch (error) {
      backendStatusMessage = 'AI重试失败：${_userMessage(error)}';
    } finally {
      aiRetryingJobIds.remove(job.jobId);
      notifyListeners();
    }
  }

  Future<void> _syncWorkFilter() async {
    final base = _api.normalizeAddress(
      backendStatusAddress,
      statusService: true,
    );
    await _api.saveWorkFilter(base, '__default__', workFilterProfile);
  }

  Future<void> _syncWorkFilterSilently() async {
    if (_preferences?.getString('work_filter_profile') == null) return;
    try {
      await _syncWorkFilter();
    } catch (_) {}
  }

  Future<void> _applySelectedModel(TranscriptionAsrStatus asr) async {
    final current = backendStatus;
    backendStatus = TranscriptionBackendStatus(
      counts: current?.counts ?? const {},
      jobs: current?.jobs ?? const [],
      asr: asr,
      customAsr: current?.customAsr ?? const CustomAsrConfig(),
      customAsrs: current?.customAsrs ?? const [],
      // 保留 AI 筛选开关状态——漏传会回退默认 true，工作流会短暂露出
      // 已停用的"AI处理"步骤（2026-11-20 用户反馈）。
      aiFilterEnabled: current?.aiFilterEnabled ?? true,
    );
    await _preferences?.setString('transcription_model', asr.selectedModelId);
  }

  void _updateBackendModelMessage(TranscriptionAsrStatus? asr) {
    if (_pendingBackendModelId.isNotEmpty) {
      if (asr?.selectedModelId == _pendingBackendModelId) {
        _confirmPendingBackendModel(asr);
      } else {
        backendStatusMessage =
            '正在确认切换到 ${_pendingBackendModelName.isEmpty ? '新模型' : _pendingBackendModelName}';
      }
      return;
    }
    final selected = asr?.models
        .where((model) => model.id == asr.selectedModelId)
        .firstOrNull;
    if (selected == null) {
      backendStatusMessage = '已更新转写状态';
    } else if (selected.healthy == false) {
      backendStatusMessage = '当前模型 ${selected.name} 服务暂不可用';
    } else {
      backendStatusMessage = '当前使用 ${selected.name}';
    }
  }

  void _confirmPendingBackendModel(TranscriptionAsrStatus? asr) {
    if (asr == null || asr.selectedModelId != _pendingBackendModelId) return;
    final name = _pendingBackendModelName;
    _pendingBackendModelId = '';
    _pendingBackendModelName = '';
    backendStatusMessage = '已切换到 ${name.isEmpty ? '新模型' : name}';
  }

  TranscriptionJobStatus? _findWorkflowJob(
    TranscriptionBackendStatus? currentStatus,
  ) {
    final jobs = currentStatus?.jobs ?? const <TranscriptionJobStatus>[];
    final workflowActive =
        bleRecording ||
        workflowWritingToTfCard ||
        workflowUploadStarted ||
        workflowUploadAwaitingConfirmation;
    return matchWorkflowJob(
      jobs: jobs,
      recordingName: workflowFile,
      baselineRecordingNames: _workflowBaselineJobs,
      workflowActive: workflowActive,
    );
  }

  Future<void> checkUnifiedRelease() async {
    if (unifiedReleaseChecking) return;
    unifiedReleaseChecking = true;
    unifiedReleaseMessage = '正在检查更新';
    notifyListeners();
    try {
      final base = _api.normalizeAddress(
        defaultReleaseAddress,
        statusService: true,
      );
      unifiedRelease = await _api.unifiedRelease(base);
      final package = await PackageInfo.fromPlatform();
      final appUpdateUrl = Platform.isIOS
          ? unifiedRelease!.iosAppUrl
          : unifiedRelease!.appUrl;
      unifiedAppUpdateNeeded =
          appUpdateUrl.isNotEmpty &&
          _versionNewer(unifiedRelease!.appVersion, package.version);
      unifiedFirmwareUpdateNeeded =
          bleFirmware.isNotEmpty &&
          _versionNewer(unifiedRelease!.firmwareVersion, bleFirmware);
      unifiedReleaseUpdateNeeded =
          unifiedAppUpdateNeeded || unifiedFirmwareUpdateNeeded;
      if (!unifiedFirmwareUpdateNeeded) bleOtaRequested = false;
      unifiedReleaseLastCheckSucceeded = true;
      unifiedReleaseMessage = unifiedReleaseUpdateNeeded ? '发现可用更新' : '已是最新版本';
    } catch (error) {
      unifiedRelease = null;
      unifiedAppUpdateNeeded = false;
      unifiedFirmwareUpdateNeeded = false;
      unifiedReleaseUpdateNeeded = false;
      unifiedReleaseLastCheckSucceeded = false;
      unifiedReleaseMessage = '检查失败：${_userMessage(error)}';
    } finally {
      unifiedReleaseChecking = false;
      notifyListeners();
    }
  }

  Future<void> installUnifiedUpdate() async {
    final release = unifiedRelease;
    if (release == null || !unifiedReleaseUpdateNeeded) return;
    final package = await PackageInfo.fromPlatform();
    final appUpdateUrl = Platform.isIOS ? release.iosAppUrl : release.appUrl;
    if (appUpdateUrl.isNotEmpty &&
        _versionNewer(release.appVersion, package.version)) {
      unifiedReleaseInstallingApp = true;
      notifyListeners();
      try {
        final uri = Uri.parse(appUpdateUrl);
        if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
          throw const FileSystemException('无法打开应用更新地址');
        }
      } finally {
        unifiedReleaseInstallingApp = false;
        notifyListeners();
      }
      return;
    }
    if (unifiedFirmwareUpdateNeeded) {
      if (!bleConnected) {
        throw StateError('请先连接录音豆，再安装固件更新。');
      }
      bleOtaRequested = await _ble.send('OTA:INSTALL');
      if (!bleOtaRequested) {
        throw StateError('固件更新请求未发送，请重试。');
      }
      unifiedReleaseMessage = '正在启动固件更新';
      _otaInstallTimer?.cancel();
      _otaInstallTimer = Timer(const Duration(seconds: 45), () {
        if (!bleOtaRequested) return;
        bleOtaRequested = false;
        unifiedReleaseMessage = bleOtaStatus.isEmpty
            ? '设备未反馈更新结果，请重新连接后检查版本'
            : '固件更新超时：${_formatOtaStatus(bleOtaStatus)}';
        notifyListeners();
      });
      notifyListeners();
    }
  }

  bool _versionNewer(String candidate, String current) {
    final left = _versionParts(candidate);
    final right = _versionParts(current);
    for (
      var index = 0;
      index < (left.length > right.length ? left.length : right.length);
      index++
    ) {
      final difference =
          (index < left.length ? left[index] : 0) -
          (index < right.length ? right[index] : 0);
      if (difference != 0) return difference > 0;
    }
    return false;
  }

  List<int> _versionParts(String value) {
    final match = RegExp(r'(\d+)\.(\d+)(?:\.(\d+))?').firstMatch(value);
    if (match == null) return const [0, 0, 0];
    return [
      int.tryParse(match.group(1) ?? '') ?? 0,
      int.tryParse(match.group(2) ?? '') ?? 0,
      int.tryParse(match.group(3) ?? '') ?? 0,
    ];
  }

  String _formatOtaStatus(String status) {
    if (status.isEmpty) return '无设备反馈';
    if (status == 'network_failed') return '设备无法连接 Wi-Fi';
    if (status == 'manifest_failed') return '无法读取更新清单';
    if (status == 'manifest_invalid') return '更新清单无效';
    if (status == 'not_configured') return '设备未配置 OTA 地址';
    if (status == 'busy') return '设备正在录音';
    if (status.startsWith('update_failed_')) {
      return '设备下载或写入失败（${status.substring('update_failed_'.length)}）';
    }
    return status;
  }

  void setPage(int value) {
    pageIndex = value;
    // 切到文件页：只在列表为空（首次进入/重连后）时自动读取一次；
    // 已有缓存列表时直接展示，需要最新数据用刷新按钮或下拉刷新
    //（2026-11-20 用户反馈：每次切 tab 都重读一遍太吵）。
    if (value == 1 && downloadingFileName.isEmpty && files.isEmpty) {
      unawaited(refreshFiles());
    }
    notifyListeners();
  }

  void enterSelectionMode() {
    selectionMode = true;
    selectedFileNames.clear();
    notifyListeners();
  }

  void exitSelectionMode() {
    selectionMode = false;
    selectedFileNames.clear();
    notifyListeners();
  }

  void toggleSelection(RecordingFile file) {
    if (!selectedFileNames.add(file.name)) selectedFileNames.remove(file.name);
    notifyListeners();
  }

  void selectAll() {
    selectionMode = true;
    selectedFileNames
      ..clear()
      ..addAll(files.map((file) => file.name));
    notifyListeners();
  }

  void selectCleanupCandidates() {
    selectionMode = true;
    selectedFileNames
      ..clear()
      ..addAll(
        files
            .where((file) => file.durationSeconds == 0 || file.size <= 128)
            .map((file) => file.name),
      );
    notifyListeners();
  }

  String _backendFailureReason(Object error) {
    final detail = error.toString().toLowerCase();
    if (detail.contains('failed host lookup')) {
      return '服务地址无法解析，请检查 ASR 服务地址是否正确';
    }
    if (detail.contains('connection refused')) {
      return '电脑上的转写服务可能没有运行，或端口不对';
    }
    if (detail.contains('timeout') || detail.contains('timed out')) {
      return '连接超时，请确认手机与电脑连的是同一个 Wi-Fi';
    }
    if (detail.contains('socket') ||
        detail.contains('network') ||
        detail.contains('unreachable')) {
      return '网络不可达，请确认手机与电脑连的是同一个 Wi-Fi';
    }
    if (error is FormatException) {
      return error.message;
    }
    return '请确认手机与电脑连的是同一个 Wi-Fi，并检查 ASR 服务地址';
  }

  String _userMessage(Object error) {
    final detail = error.toString().toLowerCase();
    if (detail.contains('timeout')) return '连接超时，请稍后重试';
    if (detail.contains('connection refused')) return '设备拒绝连接，请确认设备已启动';
    if (detail.contains('http 404')) return '未找到所需服务';
    if (error is FormatException) return error.message;
    return '操作失败，请稍后重试';
  }

  String _liveDeviceId() {
    final id = status.deviceId.trim();
    return id.isNotEmpty ? id : pairedRecorderDeviceId.trim();
  }

  /// 清空实时与历史快照（只在"新录音开始"时调用；
  /// 断线保留内容，让转写页还能回看已收到的文字）。
  void _resetLiveTranscript() {
    liveActive = false;
    livePartial = '';
    liveSentences = const [];
    liveText = '';
    liveTranscriptStem = '';
    historyStem = '';
    historySentences = const [];
    historyPartial = '';
    historyMessage = '';
    _transcriptRequestId += 1;
  }

  /// 手动刷新一帧实时转写（转写页刷新按钮 / 前台恢复）。
  Future<void> refreshLiveFrame() => _pollLiveFrame();

  /// 历史转写回看。recordingName 可以是 `REC_001.wav` 或完整对象路径。
  Future<void> loadTranscript(String recordingName) async {
    final stem = normalizeRecordingStem(recordingName);
    final requestId = ++_transcriptRequestId;
    historyStem = stem;
    historyLoading = true;
    historyMessage = '';
    historySentences = const [];
    historyPartial = '';
    notifyListeners();

    if (stem.isEmpty) {
      historyLoading = false;
      historyMessage = '无法识别录音名称';
      notifyListeners();
      return;
    }
    // 本次刚录完的那条：优先复用内存，后端重启后也不丢
    if (stem == liveTranscriptStem && hasLiveTranscript) {
      historySentences = liveSentences;
      historyPartial = livePartial;
      historyLoading = false;
      notifyListeners();
      return;
    }

    final device = _liveDeviceId();
    try {
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      if (device.isNotEmpty) {
        // 先试内存 session（覆盖"本次刚录完"），再落 DB 兜底
        final frame = TranscriptFrame.fromJson(
          await _api.liveFrame(base, device, stem),
        );
        if (requestId != _transcriptRequestId) return;
        if (!frame.isEmpty) {
          historySentences = frame.sentences;
          historyPartial = frame.partial;
          historyLoading = false;
          notifyListeners();
          return;
        }
      }
      final result = await _api.transcript(base, device, stem);
      if (requestId != _transcriptRequestId) return;
      final text = (result['text'] as String? ?? '').trim();
      historySentences = splitTranscriptText(text);
      historyPartial = '';
      historyLoading = false;
      historyMessage = historySentences.isEmpty ? '该录音无转写记录' : '';
      notifyListeners();
    } catch (error) {
      if (requestId != _transcriptRequestId) return;
      historyLoading = false;
      final message = error.toString();
      historyMessage = message.contains('HTTP 404')
          ? '该录音无转写记录'
          : '转写读取失败：${_userMessage(error)}';
      notifyListeners();
    }
  }

  void _syncLiveFramePolling() {
    final shouldPoll = bleConnected && bleRecording;
    if (shouldPoll && _liveFrameTimer == null) {
      unawaited(_pollLiveFrame());
      // 1s 轮询：出字延迟大头在固件推流节奏，App 侧 1s 是局域网
      // 轻量 JSON 的合理下限（2026-11-26 提速，原 2.5s 平均多等 1.2s）。
      _liveFrameTimer = Timer.periodic(const Duration(milliseconds: 1000), (_) {
        unawaited(_pollLiveFrame());
      });
    } else if (!shouldPoll && _liveFrameTimer != null) {
      _liveFrameTimer?.cancel();
      _liveFrameTimer = null;
      // 停止录音后保留最后一帧快照（转写页还要回看），只清 partial 状态
      livePartial = '';
      liveActive = false;
      notifyListeners();
    }
  }

  Future<void> _pollLiveFrame() async {
    if (_liveFrameInflight > 0) return;
    _liveFrameInflight++;
    try {
      final device = _liveDeviceId();
      final stem = normalizeRecordingStem(bleCurrentFile);
      if (device.isEmpty || stem.isEmpty) return;
      final base = _api.normalizeAddress(
        backendStatusAddress,
        statusService: true,
      );
      final frame = TranscriptFrame.fromJson(
        await _api.liveFrame(base, device, stem),
      );
      final changed = frame.active != liveActive ||
          frame.partial != livePartial ||
          frame.text != liveText ||
          frame.sentences.length != liveSentences.length ||
          stem != liveTranscriptStem;
      if (frame.active) {
        liveActive = true;
        livePartial = frame.partial;
        liveSentences = frame.sentences;
        liveText = frame.text;
      } else {
        // 服务端会话被清理（重启）时保留已收到的句子，别把转写页清空
        liveActive = false;
      }
      liveTranscriptStem = stem;
      // 内容没变就不 notify：避免首页/文件页每 2.5 秒无谓全量重建
      if (changed) notifyListeners();
    } catch (_) {
      // 流式引擎不可用时静默退化为普通工作流展示
    } finally {
      _liveFrameInflight--;
    }
  }

  @override
  void dispose() {
    _statusTimer?.cancel();
    _liveFrameTimer?.cancel();
    _backendTimer?.cancel();
    _recordingTimer?.cancel();
    _scanTimer?.cancel();
    _fileTimeout?.cancel();
    _reconnectTimer?.cancel();
    _wifiDownloadTimer?.cancel();
    _connectivityRefreshTimer?.cancel();
    _otaInstallTimer?.cancel();
    for (final subscription in _subscriptions) {
      subscription.cancel();
    }
    _ble.dispose();
    _api.close();
    super.dispose();
  }
}
