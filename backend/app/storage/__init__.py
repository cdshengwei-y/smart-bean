"""Storage adapters used by the transcription backend."""

from .contract import NoteObject, RecordingObject, RecordingStore
from .loader import DEFAULT_STORAGE_FACTORY, load_recording_store

__all__ = [
    "NoteObject",
    "RecordingObject",
    "RecordingStore",
    "DEFAULT_STORAGE_FACTORY",
    "load_recording_store",
]
