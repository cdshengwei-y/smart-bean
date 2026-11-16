"""Stable contract for recording and Markdown object stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Protocol, runtime_checkable


@dataclass(frozen=True)
class RecordingObject:
    object_name: str
    etag: str
    size: int
    last_modified: datetime | None


@dataclass(frozen=True)
class NoteObject:
    object_name: str
    etag: str
    size: int
    last_modified: datetime | None


@runtime_checkable
class RecordingStore(Protocol):
    """Object storage operations used by the scanner, AI worker, and sync API."""

    def list_recordings(self) -> List[RecordingObject]:
        """Return every WAV object below the configured recordings prefix."""

    def download_recording(self, object_name: str, destination: Path) -> None:
        """Download one WAV object to a local path."""

    def note_object_name(self, recording_object_name: str) -> str:
        """Map a recording object name to its Markdown object name."""

    def upload_note(self, object_name: str, markdown: str) -> None:
        """Create or replace a UTF-8 Markdown note."""

    def list_notes(self, prefix: str | None = None) -> List[NoteObject]:
        """Return Markdown objects below a prefix."""

    def download_note(self, object_name: str) -> str:
        """Read one UTF-8 Markdown note."""
