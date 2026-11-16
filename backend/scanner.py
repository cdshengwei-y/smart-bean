#!/usr/bin/env python3
"""Command-line entry point for the automatic MinIO transcription scanner."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import ScannerSettings  # noqa: E402
from app.content_filter.profile import WorkProfileStore  # noqa: E402
from app.database import JobRepository  # noqa: E402
from app.obsidian_sync import ObsidianNoteSync  # noqa: E402
from app.scanner import RecordingScanner  # noqa: E402
from app.service import TranscriptionService  # noqa: E402
from app.storage.loader import load_recording_store
from app.storage.inbox import InboxArchiveStore  # noqa: E402
from app.transcription.loader import load_transcriber  # noqa: E402


def _build_scanner(settings: ScannerSettings, repository: JobRepository) -> RecordingScanner:
    store = InboxArchiveStore(settings, load_recording_store(settings))
    obsidian_sync = (
        ObsidianNoteSync(settings, store, repository)
        if settings.obsidian_vault_path is not None
        else None
    )
    return RecordingScanner(
        settings=settings,
        store=store,
        repository=repository,
        transcription_service=TranscriptionService(
            load_transcriber(settings.transcriber_factory),
        ),
        profile_store=WorkProfileStore(
            settings.database_path.with_name("work_profiles.json")
        ),
        obsidian_sync=obsidian_sync,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="自动扫描 MinIO 录音并转写为 Markdown")
    parser.add_argument("--once", action="store_true", help="扫描并处理一次后退出")
    parser.add_argument("--scan-only", action="store_true", help="只登记新录音，不调用 ASR")
    args = parser.parse_args()

    settings = ScannerSettings.load()
    repository = JobRepository(settings.database_path, settings.recordings_prefix)
    repository.recover_interrupted()
    scanner = _build_scanner(settings, repository)
    try:
        if args.once or args.scan_only:
            scanner.run_once(scan_only=args.scan_only)
        else:
            while True:
                next_settings = ScannerSettings.load()
                if next_settings != settings:
                    repository.close()
                    settings = next_settings
                    repository = JobRepository(
                        settings.database_path,
                        settings.recordings_prefix,
                    )
                    repository.recover_interrupted()
                    scanner = _build_scanner(settings, repository)
                    print(
                        "STORAGE_CONFIG_RELOADED provider=%s factory=%s"
                        % (settings.storage_provider, settings.storage_factory),
                        flush=True,
                    )
                try:
                    scanner.run_once(report_idle=False)
                except Exception as exc:
                    print("SCAN ERROR: %s" % exc, flush=True)
                time.sleep(settings.scan_interval_seconds)
    except KeyboardInterrupt:
        print("Scanner stopped.")
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
