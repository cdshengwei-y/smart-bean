from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path

from app.content_filter.contract import ContentFilterError, WorkContentFilterResult
from app.content_filter.openai_compatible import OpenAICompatibleWorkContentFilter
from app.content_filter.openai_compatible import _RetryableContentFilterError
from app.service import TranscriptionService
from app.transcription.contract import TranscriptionRequest, TranscriptionResult


class MixedTranscriber:
    provider_name = "fake-asr"

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(
            text="中午吃火锅。下午完成登录接口联调，明天修复超时问题。",
            language="zh",
            model_name=self.provider_name,
        )


class FakeWorkFilter:
    provider_name = "fake-work-filter"

    def __init__(self, result: str) -> None:
        self.result = result

    def filter(self, transcript: str) -> WorkContentFilterResult:
        return WorkContentFilterResult(text=self.result, model_name=self.provider_name)


class ContentFilterTest(unittest.TestCase):
    @staticmethod
    def _cached_completion(filterer: OpenAICompatibleWorkContentFilter, transcript: str = "同一段文字。"):
        return filterer._cached_completion(
            "filter",
            "测试指令",
            transcript,
            "test_schema",
            {"keep_segments": {"type": "array", "items": {"type": "string"}}},
            ["keep_segments"],
        )

    def test_concurrent_same_digest_calls_completion_once(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": cache_dir,
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            callers_ready = threading.Barrier(4)
            inflight_waiters_ready = threading.Barrier(4)
            calls = 0
            calls_lock = threading.Lock()

            class TrackingFuture(Future):
                def result(self, timeout=None):
                    inflight_waiters_ready.wait(timeout=5)
                    return super().result(timeout)

            def fake_completion(*args, **kwargs):
                nonlocal calls
                with calls_lock:
                    calls += 1
                # The leader is the fourth barrier participant. Releasing it
                # proves that all three followers reached Future.result().
                inflight_waiters_ready.wait(timeout=5)
                return {"keep_segments": ["同一段文字。"]}

            filterer._completion = fake_completion

            def invoke():
                callers_ready.wait(timeout=5)
                return self._cached_completion(filterer)

            with patch("app.content_filter.openai_compatible.Future", TrackingFuture):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(invoke) for _ in range(4)]
                    results = [future.result(timeout=5) for future in futures]

        self.assertEqual(calls, 1)
        self.assertEqual(results, [{"keep_segments": ["同一段文字。"]}] * 4)
        self.assertEqual(filterer._inflight, {})

    def test_failed_inflight_is_cleared_and_next_call_can_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": cache_dir,
                "WORK_FILTER_RETRIES": "1",
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            calls = 0

            def fail_then_succeed(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ContentFilterError("模型超时")
                return {"keep_segments": ["同一段文字。"]}

            filterer._completion = fail_then_succeed
            with self.assertRaisesRegex(ContentFilterError, "模型超时"):
                self._cached_completion(filterer)

            self.assertEqual(filterer._inflight, {})
            result = self._cached_completion(filterer)

        self.assertEqual(result, {"keep_segments": ["同一段文字。"]})
        self.assertEqual(calls, 2)
        self.assertEqual(filterer._inflight, {})

    def test_truncated_response_retries_with_more_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": cache_dir,
                "WORK_FILTER_RETRIES": "2",
                "WORK_FILTER_MAX_TOKENS": "512",
                "WORK_FILTER_MAX_RETRY_TOKENS": "2048",
                "WORK_FILTER_RETRY_DELAY_SECONDS": "0",
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            token_limits = []

            def truncate_then_succeed(*args, **kwargs):
                token_limits.append(kwargs["max_tokens"])
                if len(token_limits) == 1:
                    raise _RetryableContentFilterError(
                        "输出被截断", increase_tokens=True
                    )
                return {"keep_segments": ["同一段文字。"]}

            filterer._completion = truncate_then_succeed
            result = self._cached_completion(filterer)

        self.assertEqual(result, {"keep_segments": ["同一段文字。"]})
        self.assertEqual(token_limits, [512, 1024])

    def test_rate_limit_waits_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir, patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": cache_dir,
                "WORK_FILTER_RETRIES": "2",
                "WORK_FILTER_RETRY_DELAY_SECONDS": "0",
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            calls = 0

            def limited_then_succeed(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise _RetryableContentFilterError(
                        "并发繁忙", retry_after_seconds=5
                    )
                return {"keep_segments": ["同一段文字。"]}

            filterer._completion = limited_then_succeed
            with patch("app.content_filter.openai_compatible.time.sleep") as sleep:
                result = self._cached_completion(filterer)

        self.assertEqual(result, {"keep_segments": ["同一段文字。"]})
        sleep.assert_called_once_with(5)

    def test_applies_a_unique_edit_once(self) -> None:
        result = OpenAICompatibleWorkContentFilter._apply_edits(
            "他说话，多停顿。", [{"from": "话，多", "to": "花朵"}]
        )
        self.assertEqual(result, "他说花朵停顿。")

    def test_skips_missing_and_ambiguous_edits(self) -> None:
        original = "花朵很好，花朵开了。"
        result = OpenAICompatibleWorkContentFilter._apply_edits(
            original,
            [
                {"from": "不存在", "to": "内容"},
                {"from": "花朵", "to": "话多"},
            ],
        )
        self.assertEqual(result, original)

    def test_no_edits_keeps_text_byte_for_byte(self) -> None:
        original = "第一段。\n\n第二段，有空格。 "
        self.assertEqual(OpenAICompatibleWorkContentFilter._apply_edits(original, []), original)

    def test_can_compress_an_exact_repeat_span(self) -> None:
        result = OpenAICompatibleWorkContentFilter._apply_edits(
            "前面，花朵花朵花朵花朵，后面。",
            [{"from": "花朵花朵花朵花朵", "to": "花朵"}],
        )
        self.assertEqual(result, "前面，花朵，后面。")

    def test_filter_keeps_only_verbatim_unique_segments(self) -> None:
        original = "今天开发登录接口。\n\n晚上吃饭聊天。"
        self.assertEqual(
            OpenAICompatibleWorkContentFilter._keep_exact_segments(
                original, ["今天开发登录接口。", "这是模型编的总结。", "晚上吃饭聊天。"]
            ),
            "今天开发登录接口。\n\n晚上吃饭聊天。",
        )

    def test_filter_skips_ambiguous_segment_and_preserves_order(self) -> None:
        original = "任务已完成。\n\n任务已完成。\n\n接口需要修复。"
        self.assertEqual(
            OpenAICompatibleWorkContentFilter._keep_exact_segments(
                original, ["任务已完成。", "接口需要修复。"]
            ),
            "接口需要修复。",
        )

    def test_meeting_filter_deletes_only_verbatim_unique_segments(self) -> None:
        original = "先讨论接口方案。\n\n中午想吃火锅。\n\n明天完成联调。"
        self.assertEqual(
            OpenAICompatibleWorkContentFilter._drop_exact_segments(
                original, ["中午想吃火锅。", "这是模型编的内容。"]
            ),
            "先讨论接口方案。\n\n明天完成联调。",
        )

    def test_meeting_filter_keeps_ambiguous_drop_segment(self) -> None:
        original = "先休息。再讨论。最后休息。"
        self.assertEqual(
            OpenAICompatibleWorkContentFilter._drop_exact_segments(original, ["休息。"]),
            original,
        )

    def test_normal_work_filter_removes_recording_pipeline_self_test(self) -> None:
        text = (
            "这次我想测试转写加筛选的整体耗时，重点看录音结束后多久能在 Obsidian 里看到最终文字。"
            "前半段请保留工作内容，后半段如果出现非现场闲聊或者无关内容，也看筛选会不会自动去掉。"
            "今天还要顺便确认最终笔记覆盖是否正常，以及下载转写原文笔记优化筛选分别用了多久。"
            "\n\n"
            "优化筛选分别用了多久？等测试完，我们再看这条链路是不是足够快。"
        )
        result = OpenAICompatibleWorkContentFilter._drop_obvious_normal_work_noise(
            text,
            {"enabled": True, "recording_scene": "work", "work_types": ["software_development"]},
        )
        self.assertEqual(result, "")

    def test_normal_work_filter_keeps_real_task_while_removing_self_test(self) -> None:
        text = (
            "这次我想测试转写加筛选的整体耗时，重点看录音结束后多久能在 Obsidian 里看到最终文字。"
            "\n\n"
            "明天需要修复后台任务队列超时问题，并把处理耗时记录到任务详情里。"
        )
        result = OpenAICompatibleWorkContentFilter._drop_obvious_normal_work_noise(
            text,
            {"enabled": True, "recording_scene": "work", "work_types": ["software_development"]},
        )
        self.assertEqual(result, "明天需要修复后台任务队列超时问题，并把处理耗时记录到任务详情里。")

    def test_normal_work_filter_recovers_real_work_sentence_when_model_returns_empty(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": tempfile.mkdtemp(),
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()

            def fake_completion(stage, instruction, transcript, schema_name, properties, required):
                return {"keep_segments": []}

            filterer._cached_completion = fake_completion
            result = filterer.filter(
                "这次我想测试转写加筛选的整体耗时，重点看录音结束后多久能在 Obsidian 里看到最终文字。"
                "明天要修复后台任务队列超时问题，并把下载转写优化筛选各阶段耗时显示到任务详情里。"
                "今晚吃什么我还没想好。",
                {"enabled": True, "recording_scene": "work", "work_types": ["software_development"]},
            )

        self.assertIn("明天要修复后台任务队列超时问题", result.text)
        self.assertNotIn("今晚吃什么", result.text)
        self.assertNotIn("Obsidian", result.text)

    def test_all_scene_returns_optimized_text_without_model_call(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": tempfile.mkdtemp(),
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            result = filterer.filter(
                "完整会议内容。",
                {"enabled": True, "recording_scene": "all"},
            )
        self.assertEqual(result.text, "完整会议内容。")
        self.assertEqual(result.metadata["filter_enabled"], "false")

    def test_meeting_scene_treats_work_types_as_hints(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": tempfile.mkdtemp(),
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            captured = {}

            def fake_completion(stage, instruction, transcript, schema_name, properties, required):
                captured["instruction"] = instruction
                return {"drop_segments": []}

            filterer._cached_completion = fake_completion
            result = filterer.filter(
                "会议中的硬件和项目管理内容。",
                {"enabled": True, "recording_scene": "meeting", "work_types": ["software_development"]},
            )
        self.assertEqual(result.text, "会议中的硬件和项目管理内容。")
        self.assertIn("工作类型只用于提示重点", captured["instruction"])

    def test_single_worker_stops_after_first_failed_chunk(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "WORK_FILTER_BASE_URL": "http://filter.test",
                "WORK_FILTER_MODEL": "test-model",
                "WORK_FILTER_CACHE_DIR": tempfile.mkdtemp(),
                "WORK_FILTER_WORKERS": "1",
                "WORK_FILTER_FILTER_CHUNK_CHARS": "300",
            },
        ):
            filterer = OpenAICompatibleWorkContentFilter()
            calls = []

            def fail_completion(stage, instruction, transcript, schema_name, properties, required):
                calls.append(transcript)
                raise ContentFilterError("模型超时")

            filterer._cached_completion = fail_completion
            with self.assertRaises(ContentFilterError):
                filterer.filter(
                    "第一块。" * 60 + "第二块。" * 60,
                    {"enabled": True, "recording_scene": "meeting"},
                )

        self.assertEqual(len(calls), 1)

    def test_note_contains_only_filtered_work_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "全天录音.wav"
            audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            processed = TranscriptionService(
                MixedTranscriber(),
                FakeWorkFilter("下午完成登录接口联调，明天修复超时问题。"),
            ).process(TranscriptionRequest(audio_path=audio))

        self.assertIsNotNone(processed.markdown)
        self.assertIn("下午完成登录接口联调", processed.markdown or "")
        self.assertNotIn("中午吃火锅", processed.markdown or "")
        self.assertEqual(processed.result.text, "中午吃火锅。下午完成登录接口联调，明天修复超时问题。")

    def test_empty_filtered_result_replaces_raw_note_without_private_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "生活录音.wav"
            audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            processed = TranscriptionService(MixedTranscriber(), FakeWorkFilter("")).process(
                TranscriptionRequest(audio_path=audio)
            )

        self.assertIsNotNone(processed.markdown)
        self.assertNotIn("中午吃火锅", processed.markdown or "")
        self.assertIn("AI处理状态: 已完成", processed.markdown or "")
        self.assertFalse(processed.filtered.has_work_content)


if __name__ == "__main__":
    unittest.main()
