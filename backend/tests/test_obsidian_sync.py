from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import ScannerSettings
from app.database import JobRepository
from app.obsidian_sync import ObsidianNoteSync
from app.storage.minio_store import NoteObject


class FakeNoteStore:
    def __init__(self) -> None:
        self.etag = "note-v1"
        self.markdown = "# 第一个版本\n"

    def list_notes(self, prefix=None):
        return [
            NoteObject(
                object_name="notes/bean-a1b2c3/会议记录.md",
                etag=self.etag,
                size=len(self.markdown.encode("utf-8")),
                last_modified=datetime.now(timezone.utc),
            )
        ]

    def download_note(self, object_name):
        return self.markdown


class ObsidianSyncTest(unittest.TestCase):
    def test_local_delete_is_not_restored_from_minio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "Vault"
            vault.mkdir()
            settings = ScannerSettings(
                minio_endpoint="127.0.0.1:9000",
                minio_access_key="test",
                minio_secret_key="test-secret",
                minio_secure=False,
                minio_bucket="recordingbean",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=root / "jobs.db",
                scan_interval_seconds=15,
                max_attempts=3,
                retry_delay_seconds=60,
                transcriber_factory="unused",
                transcription_language="zh",
                obsidian_vault_path=vault,
                obsidian_notes_folder="AI录音豆",
                obsidian_device_id="bean-a1b2c3",
            )
            store = FakeNoteStore()
            repository = JobRepository(settings.database_path)
            synchronizer = ObsidianNoteSync(settings, store, repository)

            self.assertEqual(synchronizer.sync_once(), 1)
            destination = vault / "AI录音豆" / "bean-a1b2c3" / "会议记录.md"
            self.assertEqual(destination.read_text(encoding="utf-8"), "# 第一个版本\n")
            self.assertEqual(synchronizer.sync_once(), 0)

            destination.unlink()
            self.assertEqual(synchronizer.sync_once(), 0)
            self.assertFalse(destination.exists())
            repository.close()

    def test_existing_note_is_overwritten_when_minio_etag_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "Vault"
            vault.mkdir()
            settings = ScannerSettings(
                minio_endpoint="127.0.0.1:9000",
                minio_access_key="test",
                minio_secret_key="test-secret",
                minio_secure=False,
                minio_bucket="recordingbean",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=root / "jobs.db",
                scan_interval_seconds=15,
                max_attempts=3,
                retry_delay_seconds=60,
                transcriber_factory="unused",
                transcription_language="zh",
                obsidian_vault_path=vault,
                obsidian_notes_folder="AI录音豆",
                obsidian_device_id="bean-a1b2c3",
            )
            store = FakeNoteStore()
            repository = JobRepository(settings.database_path)
            synchronizer = ObsidianNoteSync(settings, store, repository)

            self.assertEqual(synchronizer.sync_once(), 1)
            destination = vault / "AI录音豆" / "bean-a1b2c3" / "会议记录.md"
            self.assertEqual(destination.read_text(encoding="utf-8"), "# 第一个版本\n")

            store.etag = "note-v2"
            store.markdown = "# 第二个版本\n"
            self.assertEqual(synchronizer.sync_once(), 1)
            self.assertEqual(destination.read_text(encoding="utf-8"), "# 第二个版本\n")
            repository.close()


if __name__ == "__main__":
    unittest.main()
