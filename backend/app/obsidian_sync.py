"""Synchronize generated MinIO Markdown notes into an Obsidian vault."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ScannerSettings
from .database import JobRepository


class ObsidianNoteSync:
    def __init__(self, settings: ScannerSettings, store: Any, repository: JobRepository) -> None:
        if settings.obsidian_vault_path is None:
            raise ValueError("未找到 Obsidian Vault 路径。")
        self.settings = settings
        self.store = store
        self.repository = repository
        self.destination_root = settings.obsidian_vault_path / settings.obsidian_notes_folder

    def local_path(self, object_name: str) -> Path:
        relative_name = object_name.removeprefix(self.settings.notes_prefix)
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts or relative_path.suffix.lower() != ".md":
            raise ValueError("云端笔记路径无效：%s" % object_name)
        return self.destination_root / relative_path

    def sync_once(self) -> int:
        synced = 0
        device_prefix = None
        if self.settings.obsidian_device_id:
            device_prefix = "%s%s/" % (
                self.settings.notes_prefix,
                self.settings.obsidian_device_id,
            )
        for item in self.store.list_notes(device_prefix):
            if device_prefix and not item.object_name.startswith(device_prefix):
                continue
            if not self.repository.note_needs_sync(item.object_name, item.etag):
                continue
            destination = self.local_path(item.object_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(self.store.download_note(item.object_name), encoding="utf-8")
            temporary.replace(destination)
            self.repository.mark_note_synced(item.object_name, item.etag, destination)
            synced += 1
            print("SYNC %s -> %s" % (item.object_name, destination), flush=True)
        return synced
