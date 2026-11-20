"""Stable interfaces for plugging in a local speech-to-text model."""

from .contract import (
    Transcriber,
    TranscriberError,
    TranscriberNotConfigured,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from .model_registry import CUSTOM_ASR, ModelRegistry

__all__ = [
    "Transcriber",
    "TranscriberError",
    "TranscriberNotConfigured",
    "TranscriptionRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
    "ModelRegistry",
    "CUSTOM_ASR",
]
