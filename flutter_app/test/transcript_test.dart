import 'package:flutter_test/flutter_test.dart';
import 'package:ai_recording_bean/transcript.dart';

void main() {
  group('normalizeRecordingStem', () {
    test('strips directory and wav suffix', () {
      expect(
        normalizeRecordingStem(' recordings/bean-x/REC_001.wav '),
        'REC_001',
      );
    });

    test('keeps stem without suffix', () {
      expect(normalizeRecordingStem('REC_001'), 'REC_001');
    });

    test('handles bare file name', () {
      expect(normalizeRecordingStem('REC_20261126_160153_0.wav'),
          'REC_20261126_160153_0');
    });

    test('empty input stays empty', () {
      expect(normalRecordingStemOrEmpty('   '), '');
    });
  });

  group('TranscriptFrame.fromJson', () {
    test('removes duplicated partial appended to sentences', () {
      final frame = TranscriptFrame.fromJson(const {
        'active': true,
        'partial': '你好',
        'sentences': ['第一句', '你好'],
        'text': '第一句 你好',
      });
      expect(frame.sentences, ['第一句']);
      expect(frame.partial, '你好');
      expect(frame.active, isTrue);
      expect(frame.isEmpty, isFalse);
    });

    test('keeps unrelated last sentence', () {
      final frame = TranscriptFrame.fromJson(const {
        'active': true,
        'partial': '正在说',
        'sentences': ['第一句'],
        'text': '第一句 正在说',
      });
      expect(frame.sentences, ['第一句']);
      expect(frame.partial, '正在说');
    });

    test('empty frame detection', () {
      final frame = TranscriptFrame.fromJson(const {'active': false});
      expect(frame.isEmpty, isTrue);
      expect(frame.sentences, isEmpty);
      expect(frame.partial, isEmpty);
    });

    test('tolerates missing sentences field', () {
      final frame = TranscriptFrame.fromJson(const {
        'active': true,
        'partial': 'partial text',
      });
      expect(frame.sentences, isEmpty);
      expect(frame.partial, 'partial text');
    });
  });

  group('splitTranscriptText', () {
    test('splits lines and drops blanks', () {
      expect(splitTranscriptText('第一段\n\n第二段。'), ['第一段', '第二段。']);
    });

    test('splits long paragraph by sentence punctuation', () {
      final long = 'a' * 130;
      final result = splitTranscriptText('$long。${'b' * 130}！');
      expect(result.length, 2);
      expect(result[0].endsWith('。'), isTrue);
      expect(result[1].endsWith('！'), isTrue);
    });

    test('keeps short line without trailing punctuation intact', () {
      expect(splitTranscriptText('没有标点的一句'), ['没有标点的一句']);
    });

    test('empty text yields empty list', () {
      expect(splitTranscriptText('  \n '), isEmpty);
    });
  });
}

String normalRecordingStemOrEmpty(String value) => normalizeRecordingStem(value);
