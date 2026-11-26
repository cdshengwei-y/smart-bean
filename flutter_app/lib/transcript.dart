/// 转写相关纯函数与数据模型：不依赖 Flutter，便于单测。
library;

/// `REC_001.wav`、`recordings/bean-x/REC_001.wav` → `REC_001`
String normalizeRecordingStem(String name) {
  var value = name.trim();
  final slash = value.lastIndexOf('/');
  if (slash >= 0) value = value.substring(slash + 1);
  if (value.toLowerCase().endsWith('.wav')) {
    value = value.substring(0, value.length - 4);
  }
  return value.trim();
}

/// `/live/frame` 快照。
///
/// 后端 `snapshot()` 会把 partial 追加到 sentences 末尾（live_engine.py），
/// 这里去掉重复项，避免同一句显示两遍。
class TranscriptFrame {
  const TranscriptFrame({
    required this.active,
    required this.sentences,
    required this.partial,
    required this.text,
  });

  factory TranscriptFrame.fromJson(Map<String, dynamic> json) {
    final partial = (json['partial'] as String? ?? '').trim();
    final sentences =
        (json['sentences'] as List?)?.whereType<String>().toList() ??
            const <String>[];
    final trimmed = List<String>.of(sentences);
    if (partial.isNotEmpty &&
        trimmed.isNotEmpty &&
        trimmed.last.trim() == partial) {
      trimmed.removeLast();
    }
    return TranscriptFrame(
      active: json['active'] == true,
      sentences: trimmed,
      partial: partial,
      text: (json['text'] as String? ?? '').trim(),
    );
  }

  final bool active;
  final List<String> sentences;
  final String partial;
  final String text;

  bool get isEmpty => sentences.isEmpty && partial.isEmpty;
}

/// 落库的 transcript 是整段文本（可能含空行/段落），拆成列表展示。
List<String> splitTranscriptText(String text) {
  final result = <String>[];
  for (final line in text.split('\n')) {
    final trimmed = line.trim();
    if (trimmed.isEmpty) continue;
    if (trimmed.length <= 120) {
      result.add(trimmed);
      continue;
    }
    // 超长段落按句末标点再拆，避免一条几百字
    for (final piece in trimmed.split(RegExp(r'(?<=[。！？!?])'))) {
      final sentence = piece.trim();
      if (sentence.isNotEmpty) result.add(sentence);
    }
  }
  return result;
}
