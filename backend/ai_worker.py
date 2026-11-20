#!/usr/bin/env python3
"""Process saved transcripts through AI optimization and filtering."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import ScannerSettings  # noqa: E402
from app.content_filter.loader import load_content_filter  # noqa: E402
from app.content_filter.profile import WorkProfileStore  # noqa: E402
from app.database import JobRepository, StoredTextJob, ai_filter_enabled  # noqa: E402
from app.service import TextProcessingService  # noqa: E402
from app.storage.contract import RecordingStore  # noqa: E402
from app.storage.loader import load_recording_store  # noqa: E402
from app.transcription.contract import TranscriptionRequest, TranscriptionResult  # noqa: E402


class AiWorker:
    def __init__(
        self,
        settings: ScannerSettings,
        repository: JobRepository,
        store: RecordingStore,
        text_service: TextProcessingService,
        profile_store: WorkProfileStore,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.store = store
        self.text_service = text_service
        self.profile_store = profile_store
        self.max_attempts = max(1, int(os.getenv("AI_MAX_ATTEMPTS", "1")))
        self.retry_delay_seconds = max(1.0, float(os.getenv("AI_RETRY_DELAY_SECONDS", "600")))
        self.failure_cooldown_seconds = max(
            1.0, float(os.getenv("AI_FAILURE_COOLDOWN_SECONDS", "900"))
        )

    def process_job(self, job: StoredTextJob) -> bool:
        request = TranscriptionRequest(
            audio_path=Path(Path(job.object_name).name),
            source_object_name=job.object_name,
            language=job.language or self.settings.transcription_language,
            metadata={"device_id": job.device_id},
        )
        result = TranscriptionResult(
            text=job.transcript,
            language=job.language,
            duration_seconds=job.duration_seconds,
            model_name=job.transcription_model,
            metadata={"empty_transcript": "true"} if not job.transcript.strip() else {},
        )
        profile = self.profile_store.get(job.device_id)
        try:
            optimized = job.optimized_text
            if not optimized:
                started = time.monotonic()
                optimized = self.text_service.optimize(job.transcript)
                optimize_seconds = time.monotonic() - started
                self.repository.save_optimized(job.id, optimized, optimize_seconds)
            filter_started = time.monotonic()
            filtered = self.text_service.filter(optimized, profile)
            filter_seconds = time.monotonic() - filter_started
            processed = self.text_service.build(request, result, filtered)
            note_started = time.monotonic()
            self.store.upload_note(job.note_object_name, processed.markdown or "")
            final_note_seconds = time.monotonic() - note_started
            self.repository.complete_ai(
                job.id,
                filtered.text,
                filter_seconds,
                final_note_seconds,
            )
            print(
                "AI_DONE %s optimize=%.1fs filter=%.1fs"
                % (job.object_name, optimize_seconds if not job.optimized_text else 0.0, filter_seconds),
                flush=True,
            )
            return True
        except Exception as exc:
            self.repository.fail_ai(job.id, str(exc))
            print("AI_FAIL %s: %s" % (job.object_name, exc), flush=True)
            return False

    def run_once(self) -> bool | None:
        if not ai_filter_enabled():
            # AI 筛选关闭（2026-11-20 用户要求先停用）：空转即可，
            # scanner 的 mark_note_ready 会把任务直接置 completed。
            return None
        job = self.repository.claim_next_ai(self.max_attempts, self.retry_delay_seconds)
        if job is None:
            return None
        return self.process_job(job)

    def run_forever(self) -> None:
        while True:
            current = ScannerSettings.load()
            if current != self.settings:
                self.settings = current
                self.store = load_recording_store(current)
                print(
                    "STORAGE_CONFIG_RELOADED provider=%s factory=%s"
                    % (current.storage_provider, current.storage_factory),
                    flush=True,
                )
            processed = self.run_once()
            # A failed job already has its own retry timestamp/attempt limit.
            # Drain queued jobs immediately; poll only when the queue is empty.
            if processed is None:
                time.sleep(self.settings.scan_interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="处理已保存转写的AI优化和筛选")
    parser.add_argument("--once", action="store_true", help="最多处理一条任务后退出")
    args = parser.parse_args()

    settings = ScannerSettings.load()
    # The scanner owns schema migrations; this worker only reads/writes rows.
    repository = JobRepository(
        settings.database_path, settings.recordings_prefix, ensure_schema=False
    )
    worker = AiWorker(
        settings,
        repository,
        load_recording_store(settings),
        TextProcessingService(load_content_filter()),
        WorkProfileStore(settings.database_path.with_name("work_profiles.json")),
    )
    try:
        if args.once:
            worker.run_once()
        else:
            worker.run_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
