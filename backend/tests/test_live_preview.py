from __future__ import annotations

import json
import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path

from app.config import ScannerSettings
from app.database import JobRepository
from app.scanner import RecordingScanner
from app.storage.contract import RecordingObject
from app.storage.inbox import InboxArchiveStore, is_live_segment_object
from app.transcription.contract import TranscriptionRequest, TranscriptionResult


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


def write_wav(path: Path, seconds: float = 0.2) -> None:
    import math
    import struct

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / 16000)))
            for i in range(int(16000 * seconds))
        )
        handle.writeframes(frames)


class FakeTranscriber:
    provider_name = "fake-asr"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.calls += 1
        marker = Path(request.source_object_name).name
        return TranscriptionResult(
            text="分段文字[%s]" % marker,
            language="zh",
            duration_seconds=60.0,
            model_name="fake-asr",
            metadata={},
        )


class FakeLiveStore:
    """直播分段存储桩：MinIO live/ 分段 + 已归档普通录音。"""

    def __init__(self) -> None:
        self.recordings: list[RecordingObject] = []
        self.segments = [
            RecordingObject(
                object_name="recordings/bean_x/live/REC_001/REC_001__s01.wav",
                etag="e1",
                size=10,
                last_modified=datetime.now(timezone.utc),
            ),
            RecordingObject(
                object_name="recordings/bean_x/live/REC_001/REC_001__s02.wav",
                etag="e2",
                size=10,
                last_modified=datetime.now(timezone.utc),
            ),
        ]
        self.uploaded_notes: dict[str, str] = {}
        self.deleted: list[str] = []

    def list_recordings(self):
        return list(self.recordings)

    def list_live_segments(self):
        return list(self.segments)

    def download_recording(self, object_name, destination):
        write_wav(destination)

    def note_object_name(self, recording_object_name):
        return recording_object_name.replace("recordings/", "notes/").replace(
            ".wav", ".md"
        )

    def upload_note(self, object_name, markdown):
        self.uploaded_notes[object_name] = markdown

    def delete_recording(self, object_name):
        self.deleted.append(object_name)
        self.segments = [
            item for item in self.segments if item.object_name != object_name
        ]


class LiveObjectFilterTest(unittest.TestCase):
    def test_live_detection(self) -> None:
        self.assertTrue(
            is_live_segment_object(
                "recordings/bean_x/live/REC_001/REC_001__s01.wav"
            )
        )
        self.assertFalse(is_live_segment_object("recordings/bean_x/REC_001.wav"))


class LivePreviewPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.settings = make_settings(self.root)
        self.repository = JobRepository(self.settings.database_path)
        self.store = FakeLiveStore()
        self.transcriber = FakeTranscriber()

    def tearDown(self) -> None:
        self.repository.close()
        self._tmp.cleanup()

    def _scanner(self) -> RecordingScanner:
        from app.service import TranscriptionService

        return RecordingScanner(
            settings=self.settings,
            store=self.store,
            repository=self.repository,
            transcription_service=TranscriptionService(self.transcriber),
            obsidian_sync=None,
            profile_store=None,
        )

    def test_segments_aggregate_into_preview_job_and_note(self) -> None:
        scanner = self._scanner()
        processed = scanner.process_live_previews()
        self.assertEqual(processed, 1)
        self.assertEqual(self.transcriber.calls, 2)
        # 预览任务行已建立并完成转录
        note_job = self.repository.claim_next_note(3, 60)
        self.assertIsNotNone(note_job)
        self.assertEqual(note_job.note_object_name, "notes/bean_x/REC_001.md")
        self.assertIn("分段文字[REC_001__s01.wav]", note_job.transcript)
        self.assertIn("分段文字[REC_001__s02.wav]", note_job.transcript)
        self.assertTrue(scanner.process_note_job(note_job))
        self.assertIn("分段文字[REC_001__s01.wav]", self.store.uploaded_notes["notes/bean_x/REC_001.md"])
        # 状态已持久化：再次运行不会重复转写
        processed_again = scanner.process_live_previews()
        self.assertEqual(processed_again, 0)
        self.assertEqual(self.transcriber.calls, 2)
        state = json.loads(
            (self.root / "live_preview_state.json").read_text(encoding="utf-8")
        )
        self.assertIn("recordings/bean_x/REC_001.wav", state)

    def test_authoritative_recording_takes_over_and_cleans_segments(self) -> None:
        from app.storage.contract import RecordingObject as RecObject

        scanner = self._scanner()
        scanner.process_live_previews()
        base_object = "recordings/bean_x/REC_001.wav"
        # 完整录音出现在存储中 + 权威笔记已完成（AI 终稿）——清理的触发条件
        self.store.recordings.append(
            RecObject(
                object_name=base_object,
                etag="full",
                size=100,
                last_modified=datetime.now(timezone.utc),
            )
        )
        self.repository.discover(
            [
                RecObject(
                    object_name=base_object,
                    etag="full",
                    size=100,
                    last_modified=datetime.now(timezone.utc),
                )
            ]
        )
        self.repository.connection.execute(
            "UPDATE transcription_jobs SET status = 'completed' WHERE object_name = ?",
            (base_object,),
        )
        self.repository.connection.commit()
        scanner.process_live_previews()
        self.assertEqual(
            sorted(self.store.deleted),
            [
                "recordings/bean_x/live/REC_001/REC_001__s01.wav",
                "recordings/bean_x/live/REC_001/REC_001__s02.wav",
            ],
        )
        state = json.loads(
            (self.root / "live_preview_state.json").read_text(encoding="utf-8")
        )
        self.assertNotIn(base_object, state)

    def test_archive_store_wraps_live_pipeline(self) -> None:
        # InboxArchiveStore 包装后，live 过滤与透传仍应工作。
        scanner = self._scanner()
        wrapped = InboxArchiveStore(self.settings, self.store)
        live = wrapped.list_live_segments()
        self.assertEqual(len(live), 2)
        self.store.recordings.append(
            RecordingObject(
                object_name="recordings/bean_x/REC_001.wav",
                etag="full",
                size=100,
                last_modified=datetime.now(timezone.utc),
            )
        )
        recordings = wrapped.list_recordings()
        self.assertEqual(
            [item.object_name for item in recordings],
            ["recordings/bean_x/REC_001.wav"],
        )


if __name__ == "__main__":
    unittest.main()
