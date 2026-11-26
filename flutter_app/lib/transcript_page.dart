import 'dart:async';

import 'package:flutter/material.dart';

import 'controller.dart';

/// 打开转写页：recordingName 为空 = 本次/实时录音；非空 = 历史录音回看。
Future<void> openTranscriptPage(
  BuildContext context,
  RecordingBeanController controller, {
  String? recordingName,
}) {
  return Navigator.of(context).push<void>(
    MaterialPageRoute<void>(
      builder: (context) => TranscriptPage(
        controller: controller,
        recordingName: recordingName,
      ),
    ),
  );
}

/// 实时转写 / 历史转写回看页。
///
/// 录音中每 2.5 秒由 controller 轮询 `/live/frame` 推新；列表默认自动
/// 跟随最新一句，用户上滑即暂停跟随，右下角出现「回到最新」。
class TranscriptPage extends StatefulWidget {
  const TranscriptPage({
    super.key,
    required this.controller,
    this.recordingName,
  });

  final RecordingBeanController controller;
  final String? recordingName;

  @override
  State<TranscriptPage> createState() => _TranscriptPageState();
}

class _TranscriptPageState extends State<TranscriptPage>
    with WidgetsBindingObserver {
  static const double _followThreshold = 48;

  final ScrollController _scroll = ScrollController();
  bool _follow = true;
  bool _showJump = false;
  bool _scrollScheduled = false;
  bool _didInitialJump = false;

  bool get _isLive => widget.recordingName == null;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.controller.addListener(_onControllerChanged);
    if (!_isLive) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        unawaited(widget.controller.loadTranscript(widget.recordingName!));
      });
    }
    // 进入页面先定位到底部，避免长记录从第一句开始看
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final target = _scroll.position.maxScrollExtent;
      if (target <= 0) return;
      _didInitialJump = true;
      _scroll.jumpTo(target);
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.controller.removeListener(_onControllerChanged);
    _scroll.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) return;
    // 后台时 Dart Timer 被挂起，回前台主动补一帧并恢复跟随
    if (_isLive) {
      unawaited(widget.controller.refreshLiveFrame());
      if (!_follow) _setFollow(true);
    }
  }

  /// 新内容到达：仅在"跟随中"时滚到底部；暂停时保持阅读位置不动。
  void _onControllerChanged() {
    if (!_follow || _scrollScheduled) return;
    _scrollScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scrollScheduled = false;
      if (!mounted || !_follow || !_scroll.hasClients) return;
      final target = _scroll.position.maxScrollExtent;
      if (target <= 0) return;
      if (!_didInitialJump) {
        _didInitialJump = true;
        _scroll.jumpTo(target);
        return;
      }
      if ((target - _scroll.offset).abs() < 8) return;
      _scroll.animateTo(
        target,
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
      );
    });
  }

  bool _onScroll(ScrollNotification notification) {
    if (notification.depth != 0) return false;
    final metrics = notification.metrics;
    if (metrics.axis != Axis.vertical) return false;
    if (notification is ScrollEndNotification) {
      _setFollow(metrics.maxScrollExtent - metrics.pixels <= _followThreshold);
      return false;
    }
    // 只认用户手指拖动，程序化 animateTo 不改变跟随状态
    final dragging = (notification is ScrollUpdateNotification &&
            notification.dragDetails != null) ||
        (notification is ScrollStartNotification &&
            notification.dragDetails != null);
    if (!dragging) return false;
    _setFollow(metrics.maxScrollExtent - metrics.pixels <= _followThreshold);
    return false;
  }

  void _setFollow(bool value) {
    if (_follow == value) return;
    setState(() {
      _follow = value;
      _showJump = !value;
    });
  }

  Future<void> _jumpToLatest() async {
    setState(() {
      _follow = true;
      _showJump = false;
    });
    if (!_scroll.hasClients) return;
    await _scroll.animateTo(
      _scroll.position.maxScrollExtent,
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOut,
    );
  }

  Future<void> _reload(RecordingBeanController c) {
    if (_isLive) return c.refreshLiveFrame();
    return c.loadTranscript(widget.recordingName!);
  }

  Future<void> _confirmStop(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('停止录音'),
        content: const Text('要停止当前录音吗？停止后转写会保留。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('停止录音'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await widget.controller.toggleRecording();
  }

  String _subtitle(RecordingBeanController c, int count) {
    if (_isLive) {
      if (c.bleRecording) {
        return '正在录音 ${_clock(c.bleRecordingSeconds)} · 已出 $count 句';
      }
      return '录音已结束 · 共 $count 句';
    }
    final name = widget.recordingName ?? c.historyStem;
    return count > 0 ? '$name · 共 $count 句' : name;
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final theme = Theme.of(context);
        final scheme = theme.colorScheme;
        final sentences =
            _isLive ? controller.liveSentences : controller.historySentences;
        final rawPartial =
            _isLive ? controller.livePartial : controller.historyPartial;
        // 停止录音后最后一段 partial 不会再有更新，按定稿句展示
        final updating = _isLive && controller.bleRecording;
        final pending = updating ? rawPartial : '';
        final trailing = updating ? '' : rawPartial;
        final items = trailing.isEmpty
            ? sentences
            : <String>[...sentences, trailing];
        final offline = _isLive &&
            !controller.bleRecording &&
            !controller.bleConnected &&
            items.isNotEmpty;

        return Scaffold(
          appBar: AppBar(
            title: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _isLive ? '实时转写' : '录音转写',
                  style: TextStyle(
                    fontWeight: FontWeight.w700,
                    color: scheme.onSurface,
                  ),
                ),
                Text(
                  _subtitle(controller, items.length),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: scheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
            actions: [
              IconButton(
                onPressed: controller.historyLoading
                    ? null
                    : () => unawaited(_reload(controller)),
                tooltip: '刷新转写',
                icon: controller.historyLoading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
              ),
              if (_isLive && controller.bleRecording)
                IconButton(
                  onPressed: () => unawaited(_confirmStop(context)),
                  tooltip: '停止录音',
                  icon: Icon(Icons.stop_circle_outlined, color: scheme.error),
                ),
            ],
          ),
          body: Column(
            children: [
              if (offline)
                Container(
                  width: double.infinity,
                  color: scheme.surfaceContainerHighest,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 8,
                  ),
                  child: Text(
                    '录音豆已断开，以下为已收到的转写',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: scheme.onSurfaceVariant,
                    ),
                  ),
                ),
              Expanded(
                child: Stack(
                  children: [
                    if (items.isEmpty && pending.isEmpty)
                      _buildEmpty(controller)
                    else
                      NotificationListener<ScrollNotification>(
                        onNotification: _onScroll,
                        child: ListView.builder(
                          controller: _scroll,
                          padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                          itemCount: items.length + (pending.isEmpty ? 0 : 1),
                          itemBuilder: (context, index) {
                            if (pending.isNotEmpty && index == items.length) {
                              return _TranscriptLine(
                                text: pending,
                                pending: true,
                              );
                            }
                            return _TranscriptLine(text: items[index]);
                          },
                        ),
                      ),
                    Positioned(
                      right: 16,
                      bottom: 16,
                      child: AnimatedOpacity(
                        opacity: _showJump ? 1 : 0,
                        duration: const Duration(milliseconds: 150),
                        child: IgnorePointer(
                          ignoring: !_showJump,
                          child: FilledButton.tonalIcon(
                            onPressed: () =>
                                unawaited(_jumpToLatest()),
                            icon: const Icon(Icons.arrow_downward, size: 18),
                            label: const Text('回到最新'),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildEmpty(RecordingBeanController c) {
    final scheme = Theme.of(context).colorScheme;
    String text;
    Widget? leading;
    if (!_isLive) {
      if (c.historyLoading) {
        text = '正在读取转写…';
        leading = const SizedBox.square(
          dimension: 22,
          child: CircularProgressIndicator(strokeWidth: 2),
        );
      } else {
        text = c.historyMessage.isNotEmpty ? c.historyMessage : '该录音无转写记录';
      }
    } else if (c.bleRecording) {
      text = '正在等待设备推送音频，通常 2~5 秒后出字';
      leading = const SizedBox.square(
        dimension: 22,
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    } else {
      text = '本次录音没有转写内容';
    }
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (leading != null) ...[leading, const SizedBox(height: 12)],
            Text(
              text,
              textAlign: TextAlign.center,
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}

class _TranscriptLine extends StatelessWidget {
  const _TranscriptLine({required this.text, this.pending = false});

  final String text;
  final bool pending;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Text(
        text,
        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
              color: pending ? scheme.onSurfaceVariant : scheme.onSurface,
              fontStyle: pending ? FontStyle.italic : FontStyle.normal,
              height: 1.45,
            ),
      ),
    );
  }
}

String _clock(int seconds) {
  final safe = seconds < 0 ? 0 : seconds;
  final hours = safe ~/ 3600;
  final minutes = (safe % 3600) ~/ 60;
  final rest = safe % 60;
  if (hours > 0) {
    return '${hours.toString().padLeft(2, '0')}:'
        '${minutes.toString().padLeft(2, '0')}:'
        '${rest.toString().padLeft(2, '0')}';
  }
  return '${minutes.toString().padLeft(2, '0')}:'
      '${rest.toString().padLeft(2, '0')}';
}
