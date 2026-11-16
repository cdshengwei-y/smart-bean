from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ScannerSettings
from app.storage.contract import NoteObject, RecordingObject
from app.storage.loader import load_recording_store


class CustomStore:
    def list_recordings(self):
        return []

    def download_recording(self, object_name, destination):
        raise AssertionError("not used")

    def note_object_name(self, recording_object_name):
        return "notes/example.md"

    def upload_note(self, object_name, markdown):
        return None

    def list_notes(self, prefix=None):
        return []

    def download_note(self, object_name):
        return "# test\n"


def create_store(settings: ScannerSettings) -> CustomStore:
    return CustomStore()


class StorageLoaderTests(unittest.TestCase):
    def test_custom_storage_factory_does_not_require_minio_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = "%s:create_store" % __name__
            with patch.dict(
                os.environ,
                {
                    "STORAGE_FACTORY": factory,
                    "MINIO_ENDPOINT": "http:///",
                    "TRANSCRIPTION_DB_PATH": str(Path(directory) / "jobs.db"),
                },
                clear=True,
            ):
                settings = ScannerSettings.load()

        self.assertEqual(settings.storage_factory, factory)
        self.assertEqual(settings.database_path, Path(directory) / "jobs.db")

    def test_storage_factory_receives_settings_and_must_implement_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = ScannerSettings(
                minio_endpoint="",
                minio_access_key="",
                minio_secret_key="",
                minio_secure=False,
                minio_bucket="unused",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=Path(directory) / "jobs.db",
                scan_interval_seconds=15,
                max_attempts=3,
                retry_delay_seconds=60,
                transcriber_factory="unused",
                transcription_language="zh",
                storage_factory="%s:create_store" % __name__,
            )
            store = load_recording_store(settings)

        self.assertEqual(store.note_object_name("recordings/example.wav"), "notes/example.md")
        self.assertEqual(store.list_recordings(), [])
        self.assertEqual(store.list_notes(), [])


if __name__ == "__main__":
    unittest.main()
