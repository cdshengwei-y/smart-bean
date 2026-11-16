"""Stable contract for extracting work-related content from transcripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


class ContentFilterError(RuntimeError):
    """Raised when work-content filtering cannot be completed safely."""


@dataclass(frozen=True)
class WorkContentFilterResult:
    text: str
    model_name: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_work_content(self) -> bool:
        return bool(self.text.strip())


@runtime_checkable
class WorkContentFilter(Protocol):
    @property
    def provider_name(self) -> str:
        """Human-readable filter name used in generated note metadata."""

    def filter(self, transcript: str, profile: Mapping[str, object] | None = None) -> WorkContentFilterResult:
        """Return only work-related content, without adding new facts."""
