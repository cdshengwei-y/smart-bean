from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import ScannerSettings
from app.storage.contract import NoteObject, RecordingObject
from app.storage.inbox import (
    InboxArchiveStore,
    InboxUploadSessions,
    archive_file_path,
    is_receiver_endpoint,
    object_key_from_request,
)


def make_settings(root: Path) -> ScannerSettings:
    return ScannerSettings(
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
    )


class FakeCloudStore:
    def __init__(self) -> None:
        self.recordings = [
            RecordingObject(
                object_name="recordings/legacy/OLD_001.wav",
                etag="abc",
                size=10,
                last_modified=datetime.now(timezone.utc),
            )
        ]
        self.notes = [
            NoteObject(
                object_name="notes/legacy/OLD_001.md",
                etag="n1",
                size=5,
                last_modified=datetime.now(timezone.utc),
            )
        ]

    def list_recordings(self):
        return list(self.recordings)

    def download_recording(self, object_name, destination):
        destination.write_bytes(b"legacy-wav")

    def note_object_name(self, recording_object_name):
        return recording_object_name.replace("recordings/", "notes/").replace(
            ".wav", ".md"
        )

    def upload_note(self, object_name, markdown):
        raise AssertionError("归档模式下不应触发云端上传")

    def list_notes(self, prefix=None):
        return list(self.notes)

    def download_note(self, object_name):
        return "# note"


class ReceiverEndpointTest(unittest.TestCase):
    def test_receiver_port_detection(self) -> None:
        self.assertTrue(is_receiver_endpoint("http://192.168.0.107:8787"))
        self.assertTrue(is_receiver_endpoint("192.168.0.107:8787"))
        self.assertFalse(is_receiver_endpoint("http://192.168.0.107:9000"))
        self.assertFalse(is_receiver_endpoint(""))
        self.assertFalse(is_receiver_endpoint("http://192.168.0.107"))


class ObjectKeyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = make_settings(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_path_style_key(self) -> None:
        key = object_key_from_request(
            "/recordingbean/recordings/bean_x/REC_001.wav", self.settings
        )
        self.assertEqual(key, "recordings/bean_x/REC_001.wav")

    def test_bare_key(self) -> None:
        key = object_key_from_request("/recordings/bean_x/REC_001.wav", self.settings)
        self.assertEqual(key, "recordings/bean_x/REC_001.wav")

    def test_rejects_non_wav(self) -> None:
        with self.assertRaises(ValueError):
            object_key_from_request(
                "/recordingbean/recordings/bean_x/REC_001.txt", self.settings
            )

    def test_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            object_key_from_request(
                "/recordingbean/recordings/../bean_x/REC_001.wav", self.settings
            )

    def test_rejects_outside_prefix(self) -> None:
        with self.assertRaises(ValueError):
            object_key_from_request(
                "/recordingbean/notes/bean_x/REC_001.wav", self.settings
            )

    def test_archive_path(self) -> None:
        path = archive_file_path(self.settings, "recordings/bean_x/REC_001.wav")
        self.assertTrue(
            str(path).endswith("recordings_archive/recordings/bean_x/REC_001.wav")
        )


class MultipartSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = make_settings(Path(self._tmp.name))
        self.sessions = InboxUploadSessions(self.settings)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_part_complete_writes_archive(self) -> None:
        key = "recordings/bean_x/REC_001.wav"
        upload_id = self.sessions.create(key)
        first = self.sessions.part_path(upload_id, 1)
        second = self.sessions.part_path(upload_id, 2)
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"hello-")
        second.write_bytes(b"world")
        object_key, size = self.sessions.complete(upload_id, [1, 2])
        self.assertEqual(object_key, key)
        self.assertEqual(size, 11)
        final = archive_file_path(self.settings, key)
        self.assertEqual(final.read_bytes(), b"hello-world")

    def test_complete_without_session_returns_lookup(self) -> None:
        with self.assertRaises(LookupError):
            self.sessions.complete("missing-session", [1])

    def test_missing_part_raises_file_not_found(self) -> None:
        upload_id = self.sessions.create("recordings/bean_x/REC_002.wav")
        with self.assertRaises(FileNotFoundError):
            self.sessions.complete(upload_id, [1])

    def test_abort_removes_session(self) -> None:
        upload_id = self.sessions.create("recordings/bean_x/REC_003.wav")
        self.sessions.part_path(upload_id, 1).parent.mkdir(parents=True, exist_ok=True)
        self.sessions.abort(upload_id)
        with self.assertRaises(LookupError):
            self.sessions.part_path(upload_id, 1)

    def test_part_number_bounds(self) -> None:
        upload_id = self.sessions.create("recordings/bean_x/REC_004.wav")
        with self.assertRaises(ValueError):
            self.sessions.part_path(upload_id, 0)
        with self.assertRaises(ValueError):
            self.sessions.part_path(upload_id, 10001)

    def test_invalid_upload_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.sessions.part_path("../escape", 1)


class InboxArchiveStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = make_settings(Path(self._tmp.name))
        self.cloud = FakeCloudStore()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _store(self) -> InboxArchiveStore:
        return InboxArchiveStore(self.settings, self.cloud)

    def test_lists_archive_and_cloud(self) -> None:
        store = self._store()
        local = archive_file_path(self.settings, "recordings/bean_x/REC_001.wav")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"local-wav")
        names = {item.object_name for item in store.list_recordings()}
        self.assertIn("recordings/bean_x/REC_001.wav", names)
        self.assertIn("recordings/legacy/OLD_001.wav", names)

    def test_download_prefers_archive(self) -> None:
        store = self._store()
        local = archive_file_path(self.settings, "recordings/bean_x/REC_001.wav")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"local-wav")
        target = Path(self._tmp.name) / "out.wav"
        store.download_recording("recordings/bean_x/REC_001.wav", target)
        self.assertEqual(target.read_bytes(), b"local-wav")

    def test_download_falls_back_to_cloud(self) -> None:
        store = self._store()
        target = Path(self._tmp.name) / "out.wav"
        store.download_recording("recordings/legacy/OLD_001.wav", target)
        self.assertEqual(target.read_bytes(), b"legacy-wav")

    def test_notes_delegate_to_cloud(self) -> None:
        store = self._store()
        self.assertEqual(store.note_object_name("recordings/a/b.wav"), "notes/a/b.md")
        self.assertEqual(len(store.list_notes()), 1)
        self.assertEqual(store.download_note("notes/legacy/OLD_001.md"), "# note")

    def test_cloud_failure_still_lists_archive(self) -> None:
        store = self._store()

        def broken():
            raise RuntimeError("minio down")

        store._base.list_recordings = broken  # type: ignore[method-assign]
        local = archive_file_path(self.settings, "recordings/bean_x/REC_001.wav")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(b"local-wav")
        names = {item.object_name for item in store.list_recordings()}
        self.assertEqual(names, {"recordings/bean_x/REC_001.wav"})


if __name__ == "__main__":
    unittest.main()
