from __future__ import annotations

import tempfile
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path

from app.config import ScannerSettings
from app.database import JobRepository
from app.scanner import RecordingScanner
from app.service import TranscriptionService
from app.storage.minio_store import RecordingObject
from app.transcription.contract import TranscriptionRequest, TranscriptionResult


class FakeStore:
    def __init__(self) -> None:
        self.recording = RecordingObject(
            object_name="recordings/bean-a1b2c3/会议记录.wav",
            etag="etag-1",
            size=32044,
            last_modified=datetime.now(timezone.utc),
        )
        self.notes = {}
        self.note_uploads = []
        self.download_count = 0

    def list_recordings(self):
        return [self.recording]

    def download_recording(self, object_name, destination):
        self.download_count += 1
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 160)

    def note_object_name(self, recording_object_name):
        return "notes/bean-a1b2c3/会议记录.md"

    def upload_note(self, object_name, markdown):
        self.notes[object_name] = markdown
        self.note_uploads.append((object_name, markdown))


class FlatlineStore(FakeStore):
    def download_recording(self, object_name, destination):
        self.download_count += 1
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x87\x29" * 160)


class FakeTranscriber:
    provider_name = "fake-asr"

    def __init__(self) -> None:
        self.call_count = 0

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.call_count += 1
        return TranscriptionResult(text="自动扫描转写成功。", language="zh", model_name=self.provider_name)


class ProgressTranscriber:
    provider_name = "progress-asr"

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return self.transcribe_with_progress(request)

    def transcribe_with_progress(self, request: TranscriptionRequest, on_progress=None) -> TranscriptionResult:
        if on_progress:
            on_progress(
                TranscriptionResult(
                    text="第一段先出来。",
                    language="zh",
                    duration_seconds=3600.0,
                    model_name=self.provider_name,
                    metadata={
                        "partial": "true",
                        "completed_chunks": "1",
                        "total_chunks": "4",
                    },
                )
            )
        return TranscriptionResult(
            text="第一段先出来。\n\n完整最终文字。",
            language="zh",
            duration_seconds=3600.0,
            model_name=self.provider_name,
        )


class ScannerTest(unittest.TestCase):
    def test_nonzero_constant_pcm_fails_before_asr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = ScannerSettings(
                minio_endpoint="127.0.0.1:9000",
                minio_access_key="test",
                minio_secret_key="test-secret",
                minio_secure=False,
                minio_bucket="recordingbean",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=Path(directory) / "jobs.db",
                scan_interval_seconds=15,
                max_attempts=3,
                retry_delay_seconds=60,
                transcriber_factory="unused",
                transcription_language="zh",
            )
            store = FlatlineStore()
            transcriber = FakeTranscriber()
            repository = JobRepository(settings.database_path)
            scanner = RecordingScanner(settings, store, repository, TranscriptionService(transcriber))

            result = scanner.run_once()
            jobs = repository.list_jobs()
            repository.close()

        self.assertEqual(result["failed"], 1)
        self.assertEqual(transcriber.call_count, 0)
        self.assertEqual(jobs[0]["status"], "transcription_failed")
        self.assertIn("MICROPHONE_NO_SIGNAL", jobs[0]["error"])

    def test_same_object_is_processed_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = ScannerSettings(
                minio_endpoint="127.0.0.1:9000",
                minio_access_key="test",
                minio_secret_key="test-secret",
                minio_secure=False,
                minio_bucket="recordingbean",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=Path(directory) / "jobs.db",
                scan_interval_seconds=15,
                max_attempts=3,
                retry_delay_seconds=60,
                transcriber_factory="unused",
                transcription_language="zh",
            )
            store = FakeStore()
            transcriber = FakeTranscriber()
            repository = JobRepository(settings.database_path)
            scanner = RecordingScanner(settings, store, repository, TranscriptionService(transcriber))

            first = scanner.run_once()
            second = scanner.run_once()
            jobs = repository.list_jobs()
            repository.close()

        self.assertEqual(first["discovered"], 1)
        self.assertEqual(first["ai_pending"], 1)
        self.assertEqual(second["discovered"], 0)
        self.assertEqual(store.download_count, 1)
        self.assertEqual(transcriber.call_count, 1)
        self.assertIn("自动扫描转写成功。", store.notes["notes/bean-a1b2c3/会议记录.md"])
        self.assertIn('设备 ID: "bean-a1b2c3"', store.notes["notes/bean-a1b2c3/会议记录.md"])
        self.assertEqual(jobs[0]["status"], "ai_pending")
        self.assertEqual(jobs[0]["transcript"], "自动扫描转写成功。")

    def test_partial_asr_progress_writes_a_visible_note_before_final_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = ScannerSettings(
                minio_endpoint="127.0.0.1:9000",
                minio_access_key="test",
                minio_secret_key="test-secret",
                minio_secure=False,
                minio_bucket="recordingbean",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=Path(directory) / "jobs.db",
                scan_interval_seconds=15,
                max_attempts=3,
                retry_delay_seconds=60,
                transcriber_factory="unused",
                transcription_language="zh",
            )
            store = FakeStore()
            repository = JobRepository(settings.database_path)
            scanner = RecordingScanner(
                settings,
                store,
                repository,
                TranscriptionService(ProgressTranscriber()),
            )

            result = scanner.run_once()
            jobs = repository.list_jobs()
            repository.close()

        self.assertEqual(result["ai_pending"], 1)
        self.assertGreaterEqual(len(store.note_uploads), 2)
        self.assertIn("第一段先出来。", store.note_uploads[0][1])
        self.assertIn("转写状态: 转写中", store.note_uploads[0][1])
        self.assertIn("完整最终文字。", store.note_uploads[-1][1])
        self.assertIn("转写状态: 已完成", store.note_uploads[-1][1])
        self.assertEqual(jobs[0]["transcript"], "第一段先出来。\n\n完整最终文字。")

if __name__ == "__main__":
    unittest.main()
