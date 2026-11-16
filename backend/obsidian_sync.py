#!/usr/bin/env python3
"""Run the local MinIO-to-Obsidian sync agent independently of cloud ASR."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import ScannerSettings  # noqa: E402
from app.database import JobRepository  # noqa: E402
from app.obsidian_sync import ObsidianNoteSync  # noqa: E402
from app.storage.loader import load_recording_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="将 MinIO Markdown 同步到本机 Obsidian Vault")
    parser.add_argument("--once", action="store_true", help="同步一次后退出")
    args = parser.parse_args()

    settings = ScannerSettings.load()
    if settings.obsidian_vault_path is None:
        raise ValueError("未找到 Obsidian Vault；请设置 OBSIDIAN_VAULT_PATH。")
    database_path = Path(
        os.getenv("OBSIDIAN_SYNC_DB_PATH", str(settings.database_path.with_name("obsidian_sync.db")))
    ).expanduser()
    repository = JobRepository(database_path, settings.recordings_prefix)
    synchronizer = ObsidianNoteSync(settings, load_recording_store(settings), repository)
    try:
        if args.once:
            print("SYNC synced=%d" % synchronizer.sync_once(), flush=True)
            return 0
        while True:
            try:
                synced = synchronizer.sync_once()
                if synced:
                    print("SYNC synced=%d" % synced, flush=True)
            except Exception as exc:
                print("SYNC ERROR: %s" % exc, flush=True)
            time.sleep(settings.scan_interval_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
