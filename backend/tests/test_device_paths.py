from __future__ import annotations

import unittest
import sqlite3
import tempfile
from pathlib import Path

from app.database import JobRepository
from app.device_paths import LEGACY_DEVICE_ID, device_id_from_recording_object


class DevicePathTests(unittest.TestCase):
    def test_flat_recording_is_assigned_to_legacy_device(self) -> None:
        self.assertEqual(
            device_id_from_recording_object("recordings/REC_001.wav"),
            LEGACY_DEVICE_ID,
        )

    def test_device_folder_is_used_as_the_device_id(self) -> None:
        self.assertEqual(
            device_id_from_recording_object("recordings/bean-a1b2c3/REC_001.wav"),
            "bean-a1b2c3",
        )

    def test_existing_database_is_migrated_without_losing_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE transcription_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    object_name TEXT NOT NULL,
                    etag TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    transcript TEXT NOT NULL DEFAULT '',
                    note_object_name TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(object_name, etag)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transcription_jobs
                    (object_name, etag, size, status, created_at, updated_at)
                VALUES ('recordings/bean-a1b2c3/REC_001.wav', 'etag-1', 42, 'completed', 'now', 'now')
                """
            )
            connection.commit()
            connection.close()

            repository = JobRepository(database_path)
            job = repository.list_jobs()[0]
            repository.close()

        self.assertEqual(job["device_id"], "bean-a1b2c3")


if __name__ == "__main__":
    unittest.main()
