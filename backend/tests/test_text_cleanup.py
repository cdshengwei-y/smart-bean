from __future__ import annotations
import unittest
from app.text_cleanup import clean_asr_text, remove_discourse_fillers

class TextCleanupTest(unittest.TestCase):
    def test_compresses_asr_repeat_loop(self):
        self.assertEqual(clean_asr_text("花朵花朵花朵花朵花朵"), "花朵")
    def test_keeps_two_word_emphasis(self):
        self.assertEqual(clean_asr_text("快快，关闭电源"), "快快，关闭电源")

    def test_removes_standalone_discourse_fillers(self):
        self.assertEqual(remove_discourse_fillers("他说，嗯，今天上线。"), "他说，今天上线。")
        self.assertEqual(remove_discourse_fillers("嗯，先看代码。"), "先看代码。")
        self.assertEqual(remove_discourse_fillers("他说。呃，继续。"), "他说。继续。")
        self.assertEqual(remove_discourse_fillers("这个过程，呃。模型继续。"), "这个过程。模型继续。")

    def test_keeps_filler_like_words_inside_a_sentence(self):
        self.assertEqual(remove_discourse_fillers("这个方案嗯今天再讨论。"), "这个方案嗯今天再讨论。")

if __name__ == "__main__": unittest.main()
