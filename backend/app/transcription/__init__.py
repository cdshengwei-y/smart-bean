"""Stable interfaces for plugging in a local speech-to-text model."""

from .contract import (
    Transcriber,
    TranscriberError,
    TranscriberNotConfigured,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from .model_registry import CUSTOM_ASR, ModelRegistry, ZHIPU_GLM_ASR

__all__ = [
    "Transcriber",
    "TranscriberError",
    "TranscriberNotConfigured",
    "TranscriptionRequest",
    "TranscriptionResult",
    "TranscriptionSegment",
    "ModelRegistry",
    "ZHIPU_GLM_ASR",
    "CUSTOM_ASR",
]
