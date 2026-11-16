from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.service import TranscriptionService
from app.content_filter.contract import WorkContentFilterResult
from app.transcription.contract import TranscriptionRequest, TranscriptionResult


class FakeLocalTranscriber:
    provider_name = "fake-local-model"

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(
            text="这是一段本地模型返回的测试文字。",
            language=request.language or "zh",
            duration_seconds=1.0,
            model_name=self.provider_name,
        )


class EmptyTextTranscriber:
    provider_name = "fake-local-model"

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(
            text="",
            language=request.language or "zh",
            duration_seconds=1.0,
            model_name=self.provider_name,
            metadata={"empty_transcript": "true"},
        )


class LongDurationTranscriber(FakeLocalTranscriber):
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        result = super().transcribe(request)
        return TranscriptionResult(
            text=result.text,
            language=result.language,
            duration_seconds=1800.0,
            model_name=result.model_name,
            metadata=result.metadata,
        )


class TranscriptionContractTest(unittest.TestCase):
    def test_local_adapter_result_becomes_obsidian_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "会议记录.wav"
            audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            request = TranscriptionRequest(
                audio_path=audio_path,
                source_object_name="recordings/会议记录.wav",
                language="zh",
                metadata={"device_id": "legacy"},
            )
            processed = TranscriptionService(FakeLocalTranscriber()).process(request)

        self.assertEqual(processed.result.language, "zh")
        self.assertIn("# 会议记录", processed.markdown)
        self.assertIn("这是一段本地模型返回的测试文字。", processed.markdown)
        self.assertIn('云端路径: "recordings/会议记录.wav"', processed.markdown)
        self.assertIn('设备 ID: "legacy"', processed.markdown)

    def test_empty_transcript_creates_a_minimal_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "空录音.wav"
            audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            processed = TranscriptionService(EmptyTextTranscriber()).process(
                TranscriptionRequest(audio_path=audio_path, source_object_name="recordings/空录音.wav")
            )

        self.assertEqual(processed.result.text, "")
        self.assertIn("转写状态: 未识别到文字", processed.markdown)
        self.assertTrue(processed.markdown.endswith("## 完整文字\n"))

    def test_meeting_scene_uses_meeting_note_heading(self) -> None:
        class MeetingFilter:
            provider_name = "meeting-filter"

            def filter(self, transcript: str) -> WorkContentFilterResult:
                return WorkContentFilterResult(
                    text=transcript,
                    model_name=self.provider_name,
                    metadata={"filter_enabled": "true", "recording_scene": "meeting"},
                )

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "会议.wav"
            audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            processed = TranscriptionService(FakeLocalTranscriber(), MeetingFilter()).process(
                TranscriptionRequest(audio_path=audio_path)
            )

        self.assertIn("记录场景: 会议内容", processed.markdown)
        self.assertIn("## 会议内容", processed.markdown)

    def test_all_scene_skips_model_optimization(self) -> None:
        class OptimizationAwareFilter:
            provider_name = "opt-aware"

            def __init__(self) -> None:
                self.optimize_calls = 0
                self.filter_calls = 0

            def optimize(self, transcript: str) -> str:
                self.optimize_calls += 1
                return transcript.replace("嗯，", "")

            def filter(self, transcript: str, profile=None) -> WorkContentFilterResult:
                self.filter_calls += 1
                return WorkContentFilterResult(
                    text=transcript,
                    model_name=self.provider_name,
                    metadata={
                        "filter_enabled": "false",
                        "optimization_enabled": "false",
                        "recording_scene": "all",
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "全部内容.wav"
            audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            filterer = OptimizationAwareFilter()
            processed = TranscriptionService(FakeLocalTranscriber(), filterer).process(
                TranscriptionRequest(
                    audio_path=audio_path,
                    source_object_name="recordings/全部内容.wav",
                ),
                {"recording_scene": "all", "enabled": True, "optimize_enabled": True},
            )

        self.assertEqual(filterer.optimize_calls, 0)
        self.assertEqual(filterer.filter_calls, 1)
        self.assertEqual(processed.filtered.metadata["optimization_enabled"], "false")

    def test_disabled_optimization_skips_model_optimization(self) -> None:
        class OptimizationAwareFilter:
            provider_name = "opt-aware"

            def __init__(self) -> None:
                self.optimize_calls = 0

            def optimize(self, transcript: str) -> str:
                self.optimize_calls += 1
                return transcript.replace("嗯，", "")

            def filter(self, transcript: str, profile=None) -> WorkContentFilterResult:
                return WorkContentFilterResult(
                    text=transcript,
                    model_name=self.provider_name,
                    metadata={
                        "filter_enabled": "true",
                        "optimization_enabled": "false",
                        "recording_scene": "work",
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "会议.wav"
            audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            filterer = OptimizationAwareFilter()
            processed = TranscriptionService(FakeLocalTranscriber(), filterer).process(
                TranscriptionRequest(
                    audio_path=audio_path,
                    source_object_name="recordings/会议.wav",
                ),
                {"recording_scene": "work", "enabled": True, "optimize_enabled": False},
            )

        self.assertEqual(filterer.optimize_calls, 0)
        self.assertEqual(processed.filtered.metadata["optimization_enabled"], "false")

    def test_long_recording_skips_model_optimization_by_duration(self) -> None:
        class OptimizationAwareFilter:
            provider_name = "opt-aware"

            def __init__(self) -> None:
                self.optimize_calls = 0

            def optimize(self, transcript: str) -> str:
                self.optimize_calls += 1
                return transcript.replace("嗯，", "")

            def filter(self, transcript: str, profile=None) -> WorkContentFilterResult:
                return WorkContentFilterResult(
                    text=transcript,
                    model_name=self.provider_name,
                    metadata={
                        "filter_enabled": "true",
                        "optimization_enabled": "false",
                        "recording_scene": "work",
                    },
                )

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "长录音.wav"
            audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            filterer = OptimizationAwareFilter()
            processed = TranscriptionService(LongDurationTranscriber(), filterer).process(
                TranscriptionRequest(
                    audio_path=audio_path,
                    source_object_name="recordings/长录音.wav",
                ),
                {
                    "recording_scene": "work",
                    "enabled": True,
                    "optimize_enabled": True,
                    "optimize_model_max_duration_seconds": 900,
                },
            )

        self.assertEqual(filterer.optimize_calls, 0)
        self.assertEqual(processed.filtered.metadata["optimization_enabled"], "false")


if __name__ == "__main__":
    unittest.main()
