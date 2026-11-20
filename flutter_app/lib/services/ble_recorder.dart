import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:flutter_reactive_ble/flutter_reactive_ble.dart';

import '../models.dart';

class BleCandidate {
  const BleCandidate({
    required this.id,
    required this.name,
    required this.rssi,
  });

  final String id;
  final String name;
  final int rssi;

  String get stableName =>
      name.replaceFirst('AI Recording Bean ', '').trim().toLowerCase();
}

class BleConnectionEvent {
  const BleConnectionEvent({
    required this.connected,
    required this.name,
    required this.message,
  });

  final bool connected;
  final String name;
  final String message;
}

sealed class BleFileEvent {
  const BleFileEvent();
}

class BleFileFound extends BleFileEvent {
  const BleFileFound(this.file);
  final RecordingFile file;
}

class BleFilePageFinished extends BleFileEvent {
  const BleFilePageFinished({required this.nextCursor, required this.hasMore});
  final int nextCursor;
  final bool hasMore;
}

class BleFileDeleted extends BleFileEvent {
  const BleFileDeleted(this.name);
  final String name;
}

class BleFileFailure extends BleFileEvent {
  const BleFileFailure({
    required this.operation,
    required this.name,
    required this.message,
  });
  final String operation;
  final String name;
  final String message;
}

class BleRecorderService {
  BleRecorderService({FlutterReactiveBle? ble})
    : _ble = ble ?? FlutterReactiveBle();

  /// 最近一次连接的阶段耗时打点（JSON 行），供后端 /perf 分析
  List<String> lastPerfTrace = [];

  static final serviceUuid = Uuid.parse('d3b10000-8a1f-4e23-9d46-5e6d0f434f01');
  static final controlUuid = Uuid.parse('d3b10001-8a1f-4e23-9d46-5e6d0f434f01');
  static final statusUuid = Uuid.parse('d3b10002-8a1f-4e23-9d46-5e6d0f434f01');
  static final filesUuid = Uuid.parse('d3b10003-8a1f-4e23-9d46-5e6d0f434f01');

  final FlutterReactiveBle _ble;
  final _candidateController = StreamController<BleCandidate>.broadcast();
  final _connectionController =
      StreamController<BleConnectionEvent>.broadcast();
  final _statusController = StreamController<BleDeviceStatus>.broadcast();
  final _fileController = StreamController<BleFileEvent>.broadcast();

  StreamSubscription<DiscoveredDevice>? _scanSubscription;
  StreamSubscription<ConnectionStateUpdate>? _connectionSubscription;
  StreamSubscription<List<int>>? _statusSubscription;
  StreamSubscription<List<int>>? _filesSubscription;
  QualifiedCharacteristic? _control;
  String _deviceName = '';
  Future<void> _writeTail = Future<void>.value();
  Future<void>? _disconnectFuture;
  int _connectionEpoch = 0;

  Stream<BleCandidate> get candidates => _candidateController.stream;
  Stream<BleConnectionEvent> get connections => _connectionController.stream;
  Stream<BleDeviceStatus> get statuses => _statusController.stream;
  Stream<BleFileEvent> get fileEvents => _fileController.stream;

  Future<void> startScan() async {
    await stopScan();
    _scanSubscription = _ble
        .scanForDevices(
          withServices: const [],
          scanMode: ScanMode.lowLatency,
          requireLocationServicesEnabled: false,
        )
        .listen(
          (device) {
            final isRecorder =
                device.name.startsWith('AI Recording Bean') ||
                device.serviceUuids.contains(serviceUuid);
            if (isRecorder) {
              _candidateController.add(
                BleCandidate(
                  id: device.id,
                  name: device.name.isEmpty ? 'AI Recording Bean' : device.name,
                  rssi: device.rssi,
                ),
              );
            }
          },
          onError: (Object error) {
            _connectionController.add(
              BleConnectionEvent(
                connected: false,
                name: '',
                message: '蓝牙扫描失败：$error',
              ),
            );
          },
        );
  }

  Future<void> stopScan() async {
    await _scanSubscription?.cancel();
    _scanSubscription = null;
  }

  Future<void> connect(BleCandidate candidate) async {
    await stopScan();
    await disconnect(notify: false);
    final connectionEpoch = ++_connectionEpoch;
    final connectStart = DateTime.now();
    _deviceName = candidate.name;
    _connectionController.add(
      BleConnectionEvent(
        connected: false,
        name: candidate.name,
        message: '正在连接蓝牙',
      ),
    );
    _connectionSubscription = _ble
        .connectToDevice(
          id: candidate.id,
          servicesWithCharacteristicsToDiscover: {
            serviceUuid: [controlUuid, statusUuid, filesUuid],
          },
          connectionTimeout: const Duration(seconds: 12),
        )
        .listen(
          (update) async {
            if (connectionEpoch != _connectionEpoch) return;
            switch (update.connectionState) {
              case DeviceConnectionState.connected:
                lastPerfTrace.add(
                  '{"stage": "gatt connected", "elapsed_ms": ${DateTime.now().difference(connectStart).inMilliseconds}}',
                );
                debugPrint(
                  '[BLE-PERF] gatt connected +${DateTime.now().difference(connectStart).inMilliseconds}ms',
                );
                try {
                  await _prepare(candidate.id, connectionEpoch);
                  if (connectionEpoch != _connectionEpoch) return;
                  _connectionController.add(
                    BleConnectionEvent(
                      connected: true,
                      name: candidate.name,
                      message: '蓝牙已连接',
                    ),
                  );
                } catch (error) {
                  if (connectionEpoch != _connectionEpoch) return;
                  _connectionController.add(
                    BleConnectionEvent(
                      connected: false,
                      name: candidate.name,
                      message: '读取蓝牙服务失败：$error',
                    ),
                  );
                }
              case DeviceConnectionState.connecting:
                break;
              case DeviceConnectionState.disconnecting:
                break;
              case DeviceConnectionState.disconnected:
                _connectionEpoch += 1;
                await _clearCharacteristics();
                _connectionController.add(
                  BleConnectionEvent(
                    connected: false,
                    name: candidate.name,
                    message: '蓝牙连接已断开',
                  ),
                );
            }
          },
          onError: (Object error) {
            if (connectionEpoch != _connectionEpoch) return;
            _connectionEpoch += 1;
            unawaited(_clearCharacteristics());
            _connectionController.add(
              BleConnectionEvent(
                connected: false,
                name: candidate.name,
                message: '蓝牙连接失败：$error',
              ),
            );
          },
        );
  }

  Future<void> _prepare(String deviceId, int connectionEpoch) async {
    // 连接耗时打点（2026-11-20）：定位"连上要 5 秒"的分布。
    // 打点存入 lastPerfTrace，连接完成后由 controller 经后端 /perf 上报。
    final prepareStart = DateTime.now();
    void mark(String stage) {
      final elapsed = DateTime.now().difference(prepareStart).inMilliseconds;
      lastPerfTrace.add('{"stage": "$stage", "elapsed_ms": $elapsed}');
      debugPrint('[BLE-PERF] $stage +${elapsed}ms');
    }

    mark('prepare start');
    if (Platform.isAndroid) {
      final negotiatedMtu = await _ble.requestMtu(deviceId: deviceId, mtu: 185);
      mark('MTU negotiated=$negotiatedMtu');
      if (negotiatedMtu < 100) {
        throw StateError('蓝牙数据通道长度不足（MTU $negotiatedMtu）');
      }
    }
    if (connectionEpoch != _connectionEpoch) return;
    final status = QualifiedCharacteristic(
      deviceId: deviceId,
      serviceId: serviceUuid,
      characteristicId: statusUuid,
    );
    final files = QualifiedCharacteristic(
      deviceId: deviceId,
      serviceId: serviceUuid,
      characteristicId: filesUuid,
    );
    final control = QualifiedCharacteristic(
      deviceId: deviceId,
      serviceId: serviceUuid,
      characteristicId: controlUuid,
    );
    final statusSubscription = _ble
        .subscribeToCharacteristic(status)
        .listen(_parseStatus, onError: (Object _, StackTrace _) {});
    final filesSubscription = _ble
        .subscribeToCharacteristic(files)
        .listen(_parseFileEvent, onError: (Object _, StackTrace _) {});
    if (connectionEpoch != _connectionEpoch) {
      await _cancelSafely(statusSubscription);
      await _cancelSafely(filesSubscription);
      return;
    }
    _control = control;
    _statusSubscription = statusSubscription;
    _filesSubscription = filesSubscription;
    mark('characteristics subscribed');
    try {
      final initialStatus = await _ble.readCharacteristic(status);
      mark('initial status read done');
      if (connectionEpoch == _connectionEpoch) _parseStatus(initialStatus);
    } catch (_) {
      if (connectionEpoch == _connectionEpoch) rethrow;
    }
  }

  void _parseStatus(List<int> bytes) {
    _statusController.add(
      BleDeviceStatus.parse(utf8.decode(bytes, allowMalformed: true)),
    );
  }

  void _parseFileEvent(List<int> bytes) {
    try {
      _fileController.add(decodeFileEvent(bytes));
    } catch (_) {
      _fileController.add(
        const BleFileFailure(
          operation: 'list',
          name: '',
          message: '设备返回了无法识别的文件数据',
        ),
      );
    }
  }

  static BleFileEvent decodeFileEvent(List<int> bytes) {
    final decoded = jsonDecode(utf8.decode(bytes, allowMalformed: true));
    if (decoded is! Map<String, dynamic>) throw const FormatException();
    return switch (decoded['t']) {
      'file' => BleFileFound(RecordingFile.fromJson(decoded)),
      'end' => BleFilePageFinished(
        nextCursor: (decoded['next'] as num?)?.toInt() ?? 0,
        hasMore: decoded['more'] as bool? ?? false,
      ),
      'deleted' => BleFileDeleted(decoded['n'] as String? ?? ''),
      'error' => BleFileFailure(
        operation: decoded['op'] as String? ?? 'file',
        name: decoded['n'] as String? ?? '',
        message: decoded['m'] as String? ?? '设备文件操作失败',
      ),
      _ => throw const FormatException(),
    };
  }

  Future<bool> send(String command) async {
    final control = _control;
    if (control == null) return false;
    final operation = _writeTail.then(
      (_) => _ble.writeCharacteristicWithResponse(
        control,
        value: utf8.encode(command),
      ),
    );
    _writeTail = operation.catchError((_) {});
    try {
      await operation.timeout(const Duration(seconds: 8));
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<bool> requestFiles([int cursor = 0]) => send('FILES:$cursor');
  Future<bool> requestStatus() => send('STATUS');
  Future<bool> deleteFile(String name) => send('DELETE:$name');
  Future<bool> requestWifiAccess() => send('WIFI');

  Future<bool> sendStorageConfig(Map<String, dynamic> config) async {
    final encoded = base64.encode(utf8.encode(jsonEncode(config)));
    if (encoded.isEmpty || encoded.length > 12000) return false;
    if (!await send('STORAGE:BEGIN:${encoded.length}')) return false;
    const chunkSize = 140;
    var sequence = 0;
    for (var offset = 0; offset < encoded.length; offset += chunkSize) {
      final end = offset + chunkSize < encoded.length
          ? offset + chunkSize
          : encoded.length;
      if (!await send(
        'STORAGE:CHUNK:$sequence:${encoded.substring(offset, end)}',
      )) {
        return false;
      }
      sequence++;
    }
    return send('STORAGE:COMMIT');
  }

  Future<void> disconnect({bool notify = true}) async {
    final activeDisconnect = _disconnectFuture;
    if (activeDisconnect != null) {
      await activeDisconnect;
      return;
    }
    final operation = _performDisconnect(notify: notify);
    _disconnectFuture = operation;
    try {
      await operation;
    } finally {
      if (identical(_disconnectFuture, operation)) _disconnectFuture = null;
    }
  }

  Future<void> _performDisconnect({required bool notify}) async {
    _connectionEpoch += 1;
    final connectionSubscription = _connectionSubscription;
    _connectionSubscription = null;
    await _clearCharacteristics();
    await _cancelSafely(connectionSubscription);
    if (notify) {
      _connectionController.add(
        BleConnectionEvent(
          connected: false,
          name: _deviceName,
          message: '蓝牙已断开',
        ),
      );
    }
  }

  Future<void> _clearCharacteristics() async {
    final statusSubscription = _statusSubscription;
    final filesSubscription = _filesSubscription;
    _statusSubscription = null;
    _filesSubscription = null;
    _control = null;
    await Future.wait([
      _cancelSafely(statusSubscription),
      _cancelSafely(filesSubscription),
    ]);
  }

  Future<void> _cancelSafely(StreamSubscription<dynamic>? subscription) async {
    if (subscription == null) return;
    try {
      await subscription.cancel();
    } catch (_) {
      // A physical disconnect can invalidate the native GATT subscription first.
    }
  }

  Future<void> dispose() async {
    await stopScan();
    await disconnect(notify: false);
    await _candidateController.close();
    await _connectionController.close();
    await _statusController.close();
    await _fileController.close();
  }
}
