"""Transcription orchestration shared by future workers and HTTP routes."""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from typing import Callable

from .content_filter.contract import WorkContentFilter, WorkContentFilterResult
from .note_builder import build_markdown
from .text_cleanup import clean_asr_text
from .transcription.contract import (
    Transcriber,
    TranscriberError,
    TranscriptionRequest,
    TranscriptionResult,
)


class _PassthroughContentFilter:
    provider_name = "passthrough"

    def filter(self, transcript: str, profile: dict | None = None) -> WorkContentFilterResult:
        return WorkContentFilterResult(text=transcript, model_name=self.provider_name)


@dataclass(frozen=True)
class ProcessedTranscription:
    result: TranscriptionResult
    filtered: WorkContentFilterResult
    markdown: str | None


class TextProcessingService:
    def __init__(self, content_filter: WorkContentFilter) -> None:
        self.content_filter = content_filter

    def optimize(
        self,
        transcript: str,
        profile: dict | None = None,
        *,
        duration_seconds: float | None = None,
    ) -> str:
        cleaned = clean_asr_text(transcript)
        profile = profile or {}
        scene = str(profile.get("recording_scene", "work"))
        if self._should_skip_model_optimization(scene, profile, duration_seconds):
            return cleaned
        optimize = getattr(self.content_filter, "optimize", None)
        return optimize(cleaned) if callable(optimize) else cleaned

    @staticmethod
    def _should_skip_model_optimization(
        scene: str,
        profile: dict[str, object],
        duration_seconds: float | None,
    ) -> bool:
        if scene == "all" or not bool(profile.get("optimize_enabled", True)):
            return True
        max_duration = max(
            0.0,
            float(
                profile.get(
                    "optimize_model_max_duration_seconds",
                    os.getenv("WORK_OPTIMIZE_MODEL_MAX_DURATION_SECONDS", "900"),
                )
            ),
        )
        return duration_seconds is not None and duration_seconds >= max_duration

    def filter(
        self,
        optimized: str,
        profile: dict | None = None,
        *,
        duration_seconds: float | None = None,
    ) -> WorkContentFilterResult:
        profile = profile or {}
        scene = str(profile.get("recording_scene", "work"))
        optimize_enabled = not self._should_skip_model_optimization(scene, profile, duration_seconds)
        parameters = inspect.signature(self.content_filter.filter).parameters
        filtered = (
            self.content_filter.filter(optimized, profile)
            if len(parameters) >= 2
            else self.content_filter.filter(optimized)
        )
        return WorkContentFilterResult(
            text=filtered.text,
            model_name=filtered.model_name,
            metadata={
                **filtered.metadata,
                "optimized_text": filtered.metadata.get("optimized_text", optimized),
                "optimization_enabled": "true" if optimize_enabled else "false",
                "ai_status": "completed",
            },
        )

    def build(
        self,
        request: TranscriptionRequest,
        result: TranscriptionResult,
        filtered: WorkContentFilterResult,
    ) -> ProcessedTranscription:
        # A raw note may already exist before AI processing. Always replace it
        # with the final note, including the valid "no matching content" case.
        markdown = build_markdown(request, result, filtered)
        return ProcessedTranscription(result=result, filtered=filtered, markdown=markdown)

    def process(
        self,
        request: TranscriptionRequest,
        result: TranscriptionResult,
        profile: dict | None = None,
    ) -> ProcessedTranscription:
        optimized = self.optimize(result.text, profile, duration_seconds=result.duration_seconds)
        return self.build(
            request,
            result,
            self.filter(optimized, profile, duration_seconds=result.duration_seconds),
        )


class TranscriptionService:
    def __init__(self, transcriber: Transcriber, content_filter: WorkContentFilter | None = None) -> None:
        self.transcriber = transcriber
        self.content_filter = content_filter or _PassthroughContentFilter()
        self.text_processing = TextProcessingService(self.content_filter)

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return self._transcribe(request)

    def transcribe_with_progress(
        self,
        request: TranscriptionRequest,
        on_progress: Callable[[TranscriptionResult], None] | None = None,
    ) -> TranscriptionResult:
        return self._transcribe(request, on_progress=on_progress)

    def _transcribe(
        self,
        request: TranscriptionRequest,
        on_progress: Callable[[TranscriptionResult], None] | None = None,
    ) -> TranscriptionResult:
        if not request.audio_path.is_file():
            raise TranscriberError("找不到待转写音频：%s" % request.audio_path)
        if request.audio_path.suffix.lower() != ".wav":
            raise TranscriberError("当前转写接口只接收 WAV 文件。")

        progress_transcribe = getattr(self.transcriber, "transcribe_with_progress", None)
        if callable(progress_transcribe) and on_progress is not None:
            result = progress_transcribe(request, on_progress=on_progress)
        else:
            result = self.transcriber.transcribe(request)
        if not isinstance(result, TranscriptionResult):
            raise TranscriberError("本地模型适配器必须返回 TranscriptionResult。")
        if not result.text.strip() and result.metadata.get("empty_transcript") != "true":
            raise TranscriberError("本地模型返回了空文字。")
        return result

    def pending_markdown(
        self,
        request: TranscriptionRequest,
        result: TranscriptionResult,
        profile: dict | None = None,
        ai_status: str = "waiting",
        transcription_status: str = "completed",
    ) -> str:
        scene = str((profile or {}).get("recording_scene", "work"))
        pending = WorkContentFilterResult(
            text=result.text,
            model_name="pending",
            metadata={
                "filter_enabled": "false",
                "optimization_enabled": "false",
                "recording_scene": scene,
                "optimized_text": result.text,
                "ai_status": ai_status,
                "transcription_status": transcription_status,
            },
        )
        return build_markdown(request, result, pending)

    def process(
        self, request: TranscriptionRequest, profile: dict | None = None
    ) -> ProcessedTranscription:
        result = self.transcribe(request)
        return self.text_processing.process(request, result, profile)
