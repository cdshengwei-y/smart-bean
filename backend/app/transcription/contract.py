"""Model-independent speech-to-text request and result contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Protocol, Tuple, runtime_checkable


class TranscriberError(RuntimeError):
    """Base error raised by a transcription adapter."""


class TranscriberNotConfigured(TranscriberError):
    """Raised until a concrete local model adapter is connected."""


@dataclass(frozen=True)
class TranscriptionRequest:
    audio_path: Path
    source_object_name: str = ""
    language: Optional[str] = "zh"
    prompt: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptionSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str = ""


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str = ""
    duration_seconds: Optional[float] = None
    segments: Tuple[TranscriptionSegment, ...] = ()
    model_name: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class Transcriber(Protocol):
    @property
    def provider_name(self) -> str:
        """Human-readable adapter name used in status and logs."""

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Convert one local WAV file into normalized text output."""
