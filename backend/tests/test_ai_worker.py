from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

# AI 流程测试需要 AI 队列处于启用状态
os.environ.setdefault("RECORDER_AI_FILTER", "1")

from ai_worker import AiWorker
from app.config import ScannerSettings
from app.content_filter.contract import ContentFilterError, WorkContentFilterResult
from app.database import JobRepository
from app.service import TextProcessingService
from app.storage.minio_store import RecordingObject


class FakeStore:
    def __init__(self) -> None:
        self.notes = {}

    def upload_note(self, object_name, markdown):
        self.notes[object_name] = markdown


class FakeProfiles:
    def get(self, device_id):
        return {"enabled": True, "recording_scene": "meeting"}


class ResumableFilter:
    provider_name = "fake-ai"

    def __init__(self) -> None:
        self.optimize_calls = 0
        self.filter_calls = 0
        self.fail_filter = True

    def optimize(self, transcript):
        self.optimize_calls += 1
        return transcript.replace("嗯，", "")

    def filter(self, transcript, profile=None):
        self.filter_calls += 1
        if self.fail_filter:
            raise ContentFilterError("筛选服务暂不可用")
        return WorkContentFilterResult(
            text=transcript,
            model_name=self.provider_name,
            metadata={
                "filter_enabled": "true",
                "optimization_enabled": "true",
                "recording_scene": "meeting",
            },
        )


class AiWorkerTest(unittest.TestCase):
    def test_queued_jobs_do_not_wait_between_success_or_failure(self) -> None:
        settings = SimpleNamespace(scan_interval_seconds=15)
        worker = AiWorker(settings, Mock(), Mock(), Mock(), Mock())
        with (
            patch("ai_worker.ScannerSettings.load", return_value=settings),
            patch.object(worker, "run_once", side_effect=[True, False, None]) as run_once,
            patch("ai_worker.time.sleep", side_effect=KeyboardInterrupt) as sleep,
        ):
            with self.assertRaises(KeyboardInterrupt):
                worker.run_forever()

        self.assertEqual(run_once.call_count, 3)
        sleep.assert_called_once_with(15)

    def test_filter_retry_reuses_saved_optimized_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = ScannerSettings(
                minio_endpoint="127.0.0.1:9000",
                minio_access_key="test",
                minio_secret_key="test-secret",
                minio_secure=False,
                minio_bucket="recordingbean",
                recordings_prefix="recordings/",
                notes_prefix="notes/",
                database_path=root / "jobs.db",
                scan_interval_seconds=1,
                max_attempts=3,
                retry_delay_seconds=1,
                transcriber_factory="unused",
                transcription_language="zh",
            )
            repository = JobRepository(settings.database_path)
            repository.discover(
                [
                    RecordingObject(
                        "recordings/bean-a1b2c3/会议.wav",
                        "etag-1",
                        100,
                        datetime.now(timezone.utc),
                    )
                ]
            )
            transcription = repository.claim_next_transcription(3, 1)
            assert transcription is not None
            repository.save_transcript(
                transcription.id,
                "嗯，讨论接口方案。",
                "notes/bean-a1b2c3/会议.md",
                "zh",
                "fake-asr",
                12.0,
                1.0,
                2.0,
            )
            note = repository.claim_next_note(3, 1)
            assert note is not None
            repository.mark_note_ready(note.id, 0.2)

            content_filter = ResumableFilter()
            worker = AiWorker(
                settings,
                repository,
                FakeStore(),
                TextProcessingService(content_filter),
                FakeProfiles(),
            )
            first = repository.claim_next_ai(1, 1)
            assert first is not None
            self.assertFalse(worker.process_job(first))
            failed = repository.list_jobs()[0]
            self.assertEqual(failed["status"], "ai_failed")
            self.assertEqual(failed["optimized_text"], "讨论接口方案。")

            self.assertTrue(repository.retry_ai(first.id))
            content_filter.fail_filter = False
            retry = repository.claim_next_ai(1, 1)
            assert retry is not None
            self.assertTrue(worker.process_job(retry))
            completed = repository.list_jobs()[0]
            repository.close()

        self.assertEqual(content_filter.optimize_calls, 1)
        self.assertEqual(content_filter.filter_calls, 2)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["filtered_text"], "讨论接口方案。")


if __name__ == "__main__":
    unittest.main()
