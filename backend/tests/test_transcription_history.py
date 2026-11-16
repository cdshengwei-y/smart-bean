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
            object_name="recordings/bean-history/REC_001.wav",
            etag="history-etag",
            size=2048,
            last_modified=datetime.now(timezone.utc),
        )
        self.note_uploads: list[tuple[str, str]] = []

    def list_recordings(self):
        return [self.recording]

    def download_recording(self, object_name, destination):
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 320)

    def note_object_name(self, recording_object_name):
        return "notes/bean-history/REC_001.md"

    def upload_note(self, object_name, markdown):
        self.note_uploads.append((object_name, markdown))


class ModelTranscriber:
    def __init__(self, model_id: str, calls: list[str], fail: bool = False) -> None:
        self.model_id = model_id
        self.calls = calls
        self.fail = fail
        self.provider_name = "名称 " + model_id

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.calls.append(self.model_id)
        if self.fail:
            raise RuntimeError("历史模型不可用")
        return TranscriptionResult(
            text="由 %s 生成的转写。" % self.model_id,
            language="zh",
            duration_seconds=2.0,
            model_name=self.provider_name,
        )


class SelectedHistoryTranscriber:
    def __init__(self, selected_model_id: str, calls: list[str], fail: bool = False) -> None:
        self.selected_model_id = selected_model_id
        self.calls = calls
        self.fail = fail

    def build_model(self, model_id: str):
        return ModelTranscriber(model_id, self.calls, fail=self.fail)


def settings(database_path: Path) -> ScannerSettings:
    return ScannerSettings(
        minio_endpoint="127.0.0.1:9000",
        minio_access_key="test",
        minio_secret_key="test-secret",
        minio_secure=False,
        minio_bucket="recordingbean",
        recordings_prefix="recordings/",
        notes_prefix="notes/",
        database_path=database_path,
        scan_interval_seconds=15,
        max_attempts=3,
        retry_delay_seconds=1,
        transcriber_factory="unused",
        transcription_language="zh",
    )


class TranscriptionHistoryTests(unittest.TestCase):
    def test_initial_run_records_model_and_replay_preserves_current_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            store = FakeStore()
            calls: list[str] = []
            scanner = RecordingScanner(
                settings(database_path),
                store,
                repository,
                TranscriptionService(
                    SelectedHistoryTranscriber("model-v1", calls),
                ),
            )

            scanner.run_once()
            job_before = repository.list_jobs()[0]
            source_run = repository.list_transcription_runs(job_id=int(job_before["id"]))[0]
            self.assertEqual(source_run["run_kind"], "initial")
            self.assertEqual(source_run["model_id"], "model-v1")
            self.assertEqual(source_run["transcript"], "由 model-v1 生成的转写。")
            self.assertEqual(job_before["transcription_model_id"], "model-v1")
            uploads_before_replay = list(store.note_uploads)

            replay_id = repository.queue_transcription_replay(int(source_run["id"]))
            self.assertIsNotNone(replay_id)
            scanner.run_once()

            job_after = repository.list_jobs()[0]
            runs = repository.list_transcription_runs(job_id=int(job_after["id"]))
            repository.close()

        self.assertEqual(calls, ["model-v1", "model-v1"])
        self.assertEqual(job_after["transcript"], job_before["transcript"])
        self.assertEqual(
            job_after["transcription_model_id"],
            job_before["transcription_model_id"],
        )
        self.assertEqual(job_after["note_object_name"], job_before["note_object_name"])
        self.assertEqual(store.note_uploads, uploads_before_replay)
        self.assertEqual(runs[0]["run_kind"], "replay")
        self.assertEqual(runs[0]["source_run_id"], source_run["id"])
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["model_id"], "model-v1")

    def test_failed_replay_does_not_change_current_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            store = FakeStore()
            repository.discover([store.recording])
            job = repository.claim_next_transcription(3, 1)
            assert job is not None
            repository.save_transcript(
                job.id,
                "当前正式转写。",
                store.note_object_name(job.object_name),
                "zh",
                "当前模型名称",
                2.0,
                0.1,
                0.2,
                transcription_model_id="current-model",
            )
            source_run_id = repository.start_transcription_run(job.id, "historic-model")
            repository.complete_transcription_run(
                source_run_id,
                "历史模型名称",
                "zh",
                2.0,
                0.1,
                0.2,
                "历史转写。",
            )
            replay_id = repository.queue_transcription_replay(source_run_id)
            assert replay_id is not None
            replay = repository.claim_next_transcription_replay()
            assert replay is not None
            before = repository.list_jobs()[0]
            scanner = RecordingScanner(
                settings(database_path),
                store,
                repository,
                TranscriptionService(
                    SelectedHistoryTranscriber("current-model", [], fail=True),
                ),
            )

            self.assertFalse(scanner.process_replay(replay))
            after = repository.list_jobs()[0]
            replay_row = repository.list_transcription_runs()[0]
            repository.close()

        self.assertEqual(after["transcript"], before["transcript"])
        self.assertEqual(after["transcription_model"], before["transcription_model"])
        self.assertEqual(after["note_object_name"], before["note_object_name"])
        self.assertEqual(replay_row["status"], "failed")
        self.assertIn("历史模型不可用", replay_row["error"])

    def test_replay_queue_rejects_duplicate_and_recovery_unblocks_source_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            store = FakeStore()
            repository.discover([store.recording])
            job = repository.claim_next_transcription(3, 1)
            assert job is not None
            source_run_id = repository.start_transcription_run(job.id, "historic-model")
            repository.complete_transcription_run(
                source_run_id,
                "历史模型名称",
                "zh",
                2.0,
                0.1,
                0.2,
                "历史转写。",
            )

            first_replay_id = repository.queue_transcription_replay(source_run_id)
            self.assertIsNotNone(first_replay_id)
            self.assertIsNone(repository.queue_transcription_replay(source_run_id))
            claimed = repository.claim_next_transcription_replay()
            self.assertIsNotNone(claimed)
            repository.recover_interrupted()
            recovered = repository.list_transcription_runs()[0]
            second_replay_id = repository.queue_transcription_replay(source_run_id)
            repository.close()

        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(second_replay_id, int(first_replay_id) + 1)

    def test_existing_job_is_backfilled_as_legacy_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            store = FakeStore()
            repository.discover([store.recording])
            job = repository.claim_next_transcription(3, 1)
            assert job is not None
            repository.save_transcript(
                job.id,
                "旧版已有转写。",
                store.note_object_name(job.object_name),
                "zh",
                "旧模型名称",
                2.0,
                0.1,
                0.2,
                transcription_model_id="legacy-model",
            )
            repository.connection.execute("DELETE FROM transcription_runs")
            repository.connection.commit()
            repository.close()

            migrated = JobRepository(database_path)
            runs = migrated.list_transcription_runs()
            migrated.close()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_kind"], "legacy")
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["model_id"], "legacy-model")
        self.assertEqual(runs[0]["transcript"], "旧版已有转写。")


if __name__ == "__main__":
    unittest.main()
