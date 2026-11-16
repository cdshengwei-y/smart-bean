from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.database import JobRepository
from app.storage.minio_store import NoteObject, RecordingObject
from app.sync_api import CompletedNotesService, SyncAuthorizationService


class FakeNoteStore:
    def __init__(self) -> None:
        self.notes = {
            "notes/bean-a1/REC_001.md": ("# 已完成笔记\n", "etag-completed"),
            "notes/bean-a1/REC_002.md": ("# 未完成笔记\n", "etag-pending"),
            "notes/bean-b2/REC_003.md": ("# 其他设备笔记\n", "etag-other"),
        }

    def list_notes(self, prefix=None):
        return [
            NoteObject(
                object_name=key,
                etag=etag,
                size=len(markdown.encode("utf-8")),
                last_modified=datetime.now(timezone.utc),
            )
            for key, (markdown, etag) in self.notes.items()
            if prefix is None or key.startswith(prefix)
        ]

    def download_note(self, object_name):
        return self.notes[object_name][0]


class SyncApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "jobs.db"
        self.repository = JobRepository(self.database_path)
        self.repository.discover(
            [
                RecordingObject("recordings/bean-a1/REC_001.wav", "recording-1", 42, None),
                RecordingObject("recordings/bean-a1/REC_002.wav", "recording-2", 42, None),
                RecordingObject("recordings/bean-b2/REC_003.wav", "recording-3", 42, None),
            ]
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def complete_note(self, job_id: int, note_object_name: str) -> None:
        self.repository.complete(
            job_id,
            transcript="完整转写",
            note_object_name=note_object_name,
            filtered_text="最终内容",
            optimized_text="优化内容",
        )

    def test_activation_code_is_one_time_and_token_is_device_scoped(self) -> None:
        service = SyncAuthorizationService(self.repository)
        activation_code = service.issue_activation_code("bean-a1", label="customer mac")

        device_id, token = service.activate(activation_code, "customer mac")
        self.assertEqual(device_id, "bean-a1")
        self.assertEqual(service.authenticate(token), "bean-a1")

        with self.assertRaises(PermissionError):
            service.activate(activation_code)

        self.assertTrue(service.revoke(token))
        with self.assertRaises(PermissionError):
            service.authenticate(token)

    def test_replacement_activation_revokes_the_previous_sync_client(self) -> None:
        service = SyncAuthorizationService(self.repository)
        first_code = service.issue_activation_code("bean-a1", label="old computer")
        _, first_token = service.activate(first_code, "Obsidian: Old Vault")

        replacement_code = service.issue_activation_code(
            "bean-a1",
            label="new computer",
            replace_existing=True,
        )
        _, replacement_token = service.activate(
            replacement_code,
            "Obsidian: New Vault",
        )

        with self.assertRaises(PermissionError):
            service.authenticate(first_token)
        self.assertEqual(service.authenticate(replacement_token), "bean-a1")
        clients = service.active_clients("bean-a1")
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["label"], "Obsidian: New Vault")

    def test_replacement_activation_code_supersedes_an_older_unused_code(self) -> None:
        service = SyncAuthorizationService(self.repository)
        old_code = service.issue_activation_code("bean-a1", replace_existing=True)
        new_code = service.issue_activation_code("bean-a1", replace_existing=True)

        with self.assertRaises(PermissionError):
            service.activate(old_code, "Obsidian: Old Vault")
        device_id, token = service.activate(new_code, "Obsidian: New Vault")
        self.assertEqual(device_id, "bean-a1")
        self.assertEqual(service.authenticate(token), "bean-a1")

    def test_only_completed_notes_for_bound_device_are_visible(self) -> None:
        jobs = self.repository.list_jobs()
        self.complete_note(int(jobs[0]["id"]), "notes/bean-a1/REC_001.md")
        self.complete_note(int(jobs[2]["id"]), "notes/bean-b2/REC_003.md")
        notes = CompletedNotesService(self.repository, FakeNoteStore(), "notes/").list_notes("bean-a1")

        self.assertEqual(
            [(note.id, note.relative_path, note.etag) for note in notes],
            [(int(jobs[0]["id"]), "REC_001.md", "etag-completed")],
        )

        note, markdown = CompletedNotesService(self.repository, FakeNoteStore(), "notes/").download_note(
            "bean-a1",
            int(jobs[0]["id"]),
        )
        self.assertEqual(note.relative_path, "REC_001.md")
        self.assertEqual(markdown, "# 已完成笔记\n")

        with self.assertRaises(FileNotFoundError):
            CompletedNotesService(self.repository, FakeNoteStore(), "notes/").download_note(
                "bean-a1",
                int(jobs[2]["id"]),
            )

    def test_reprocessed_note_is_hidden_until_the_latest_job_is_completed(self) -> None:
        initial = self.repository.list_jobs()[0]
        self.complete_note(int(initial["id"]), "notes/bean-a1/REC_001.md")
        self.repository.discover(
            [RecordingObject("recordings/bean-a1/REC_001.wav", "recording-1-v2", 43, None)]
        )
        latest = self.repository.list_jobs()[-1]

        service = CompletedNotesService(self.repository, FakeNoteStore(), "notes/")
        self.assertEqual(service.list_notes("bean-a1"), [])
        with self.assertRaises(FileNotFoundError):
            service.download_note("bean-a1", int(initial["id"]))

        self.complete_note(int(latest["id"]), "notes/bean-a1/REC_001.md")
        notes = service.list_notes("bean-a1")
        self.assertEqual([note.id for note in notes], [int(latest["id"])])


if __name__ == "__main__":
    unittest.main()
