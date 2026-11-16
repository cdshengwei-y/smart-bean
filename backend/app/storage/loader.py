"""Load a configured recording-store adapter without coupling to MinIO."""

from __future__ import annotations

import importlib
from typing import Any, Callable, cast

from ..config import ScannerSettings
from .contract import RecordingStore


DEFAULT_STORAGE_FACTORY = "app.storage.minio_store:create_store"
REQUIRED_METHODS = (
    "list_recordings",
    "download_recording",
    "note_object_name",
    "upload_note",
    "list_notes",
    "download_note",
)


def load_recording_store(
    settings: ScannerSettings,
    factory_spec: str | None = None,
) -> RecordingStore:
    """Build the selected storage adapter with the current scanner settings."""
    spec = (factory_spec or settings.storage_factory or DEFAULT_STORAGE_FACTORY).strip()
    try:
        module_name, attribute_name = spec.split(":", 1)
        module = importlib.import_module(module_name)
        factory = cast(Callable[[ScannerSettings], Any], getattr(module, attribute_name))
        store = factory(settings)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("无法加载存储适配器 %s：%s" % (spec, exc)) from exc

    missing = [name for name in REQUIRED_METHODS if not callable(getattr(store, name, None))]
    if missing:
        raise ValueError(
            "存储适配器 %s 缺少方法：%s。" % (spec, "、".join(missing))
        )
    return cast(RecordingStore, store)
