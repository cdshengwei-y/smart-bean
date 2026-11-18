"""SQLite job repository for idempotent transcription processing."""

from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

from .device_paths import FORMAL_DEVICE_IDS, LEGACY_DEVICE_ID, device_id_from_recording_object
from .storage.minio_store import RecordingObject


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_busy_error(exc: Exception) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


@contextmanager
def _startup_lock(database_path: Path):
    lock_path = database_path.with_name(database_path.name + ".startup.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


@dataclass(frozen=True)
class TranscriptionJob:
    id: int
    object_name: str
    device_id: str
    etag: str
    status: str
    attempts: int


@dataclass(frozen=True)
class TranscriptionRun:
    id: int
    job_id: int
    object_name: str
    device_id: str
    source_run_id: Optional[int]
    run_kind: str
    model_id: str
    model_name: str
    transcript: str
    status: str
    attempts: int


@dataclass(frozen=True)
class StoredTextJob:
    id: int
    object_name: str
    device_id: str
    transcript: str
    optimized_text: str
    note_object_name: str
    language: str
    transcription_model: str
    duration_seconds: Optional[float]
    ai_attempts: int
    ai_stage: str


class JobRepository:
    def __init__(
        self,
        database_path: Path,
        recordings_prefix: str = "recordings/",
        ensure_schema: bool = True,
    ) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path), timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 60000")
        self.recordings_prefix = recordings_prefix
        if ensure_schema:
            self._run_startup_task(self._create_schema)

    def _run_startup_task(self, task) -> None:
        for attempt in range(1, 6):
            try:
                with _startup_lock(self.database_path):
                    task()
                return
            except sqlite3.OperationalError as exc:
                if not _is_busy_error(exc) or attempt >= 5:
                    raise
                time.sleep(min(2.0, 0.25 * attempt))

    def _create_schema(self) -> None:
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transcription_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_name TEXT NOT NULL,
                device_id TEXT NOT NULL DEFAULT 'legacy',
                etag TEXT NOT NULL,
                size INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                transcript TEXT NOT NULL DEFAULT '',
                filtered_text TEXT NOT NULL DEFAULT '',
                optimized_text TEXT NOT NULL DEFAULT '',
                note_object_name TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                transcription_model TEXT NOT NULL DEFAULT '',
                transcription_model_id TEXT NOT NULL DEFAULT '',
                duration_seconds REAL,
                note_attempts INTEGER NOT NULL DEFAULT 0,
                ai_attempts INTEGER NOT NULL DEFAULT 0,
                ai_stage TEXT NOT NULL DEFAULT '',
                stage_started_at TEXT NOT NULL DEFAULT '',
                download_seconds REAL NOT NULL DEFAULT 0,
                asr_seconds REAL NOT NULL DEFAULT 0,
                raw_note_seconds REAL NOT NULL DEFAULT 0,
                optimize_seconds REAL NOT NULL DEFAULT 0,
                filter_seconds REAL NOT NULL DEFAULT 0,
                final_note_seconds REAL NOT NULL DEFAULT 0,
                ai_error TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(object_name, etag)
            )
            """
        )
        columns = {
            str(row["name"]) for row in self.connection.execute("PRAGMA table_info(transcription_jobs)")
        }
        migrations = {
            "device_id": "TEXT NOT NULL DEFAULT 'legacy'",
            "filtered_text": "TEXT NOT NULL DEFAULT ''",
            "optimized_text": "TEXT NOT NULL DEFAULT ''",
            "language": "TEXT NOT NULL DEFAULT ''",
            "transcription_model": "TEXT NOT NULL DEFAULT ''",
            "transcription_model_id": "TEXT NOT NULL DEFAULT ''",
            "duration_seconds": "REAL",
            "note_attempts": "INTEGER NOT NULL DEFAULT 0",
            "ai_attempts": "INTEGER NOT NULL DEFAULT 0",
            "ai_stage": "TEXT NOT NULL DEFAULT ''",
            "stage_started_at": "TEXT NOT NULL DEFAULT ''",
            "download_seconds": "REAL NOT NULL DEFAULT 0",
            "asr_seconds": "REAL NOT NULL DEFAULT 0",
            "raw_note_seconds": "REAL NOT NULL DEFAULT 0",
            "optimize_seconds": "REAL NOT NULL DEFAULT 0",
            "filter_seconds": "REAL NOT NULL DEFAULT 0",
            "final_note_seconds": "REAL NOT NULL DEFAULT 0",
            "ai_error": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in migrations.items():
            if name not in columns:
                self.connection.execute(
                    "ALTER TABLE transcription_jobs ADD COLUMN %s %s" % (name, definition)
                )
        for row in self.connection.execute(
            "SELECT id, object_name FROM transcription_jobs WHERE device_id = ?",
            (LEGACY_DEVICE_ID,),
        ):
            device_id = device_id_from_recording_object(
                str(row["object_name"]), self.recordings_prefix
            )
            self.connection.execute(
                "UPDATE transcription_jobs SET device_id = ? WHERE id = ?",
                (device_id, int(row["id"])),
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transcription_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                source_run_id INTEGER,
                run_kind TEXT NOT NULL DEFAULT 'initial',
                model_id TEXT NOT NULL DEFAULT '',
                model_name TEXT NOT NULL DEFAULT '',
                transcript TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                language TEXT NOT NULL DEFAULT '',
                duration_seconds REAL,
                download_seconds REAL NOT NULL DEFAULT 0,
                asr_seconds REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES transcription_jobs(id),
                FOREIGN KEY (source_run_id) REFERENCES transcription_runs(id)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS transcription_runs_job_id_idx "
            "ON transcription_runs(job_id, id DESC)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS transcription_runs_pending_idx "
            "ON transcription_runs(status, id)"
        )
        run_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(transcription_runs)")
        }
        if "transcript" not in run_columns:
            self.connection.execute(
                "ALTER TABLE transcription_runs ADD COLUMN transcript TEXT NOT NULL DEFAULT ''"
            )
        self.connection.execute(
            """
            INSERT INTO transcription_runs
                (job_id, run_kind, model_id, model_name, transcript, status, attempts,
                 language, duration_seconds, download_seconds, asr_seconds,
                 error, started_at, completed_at, created_at, updated_at)
            SELECT
                jobs.id,
                'legacy',
                COALESCE(NULLIF(jobs.transcription_model_id, ''), jobs.transcription_model),
                jobs.transcription_model,
                jobs.transcript,
                CASE
                    WHEN jobs.transcript != '' THEN 'completed'
                    WHEN jobs.status LIKE '%failed%' THEN 'failed'
                    ELSE 'unknown'
                END,
                jobs.attempts,
                jobs.language,
                jobs.duration_seconds,
                jobs.download_seconds,
                jobs.asr_seconds,
                jobs.error,
                jobs.updated_at,
                jobs.updated_at,
                jobs.created_at,
                jobs.updated_at
            FROM transcription_jobs AS jobs
            WHERE NOT EXISTS (
                SELECT 1 FROM transcription_runs AS runs WHERE runs.job_id = jobs.id
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS obsidian_sync (
                object_name TEXT PRIMARY KEY,
                etag TEXT NOT NULL,
                local_path TEXT NOT NULL,
                synced_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_activation_codes (
                code_hash TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                replace_existing INTEGER NOT NULL DEFAULT 0,
                redeemed_at TEXT NOT NULL DEFAULT '',
                revoked_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        sync_activation_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(sync_activation_codes)")
        }
        if "replace_existing" not in sync_activation_columns:
            self.connection.execute(
                "ALTER TABLE sync_activation_codes "
                "ADD COLUMN replace_existing INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_tokens (
                token_hash TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT '',
                revoked_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS managed_devices (
                device_id TEXT PRIMARY KEY,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        now = _now()
        for device_id in FORMAL_DEVICE_IDS:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO managed_devices (device_id, label, created_at)
                VALUES (?, '', ?)
                """,
                (device_id, now),
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES user_accounts(id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_recorder_bindings (
                user_id INTEGER PRIMARY KEY,
                device_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES user_accounts(id)
            )
            """
        )
        self.connection.commit()

    def recover_interrupted(self) -> None:
        def task() -> None:
            now = _now()
            self.connection.execute(
                "UPDATE transcription_jobs SET status = 'transcription_failed', "
                "error = '转写进程中断，等待重试', updated_at = ? WHERE status = 'transcribing'",
                (now,),
            )
            self.connection.execute(
                "UPDATE transcription_jobs SET status = 'note_failed', "
                "error = '原文笔记进程中断，等待重试', updated_at = ? WHERE status = 'note_processing'",
                (now,),
            )
            self.connection.execute(
                "UPDATE transcription_jobs SET status = 'ai_failed', ai_error = 'AI进程中断，等待重试', "
                "error = 'AI进程中断，等待重试', updated_at = ? "
                "WHERE status IN ('optimizing', 'filtering', 'ai_processing')",
                (now,),
            )
            self.connection.execute(
                "UPDATE transcription_jobs SET status = CASE WHEN transcript = '' THEN 'transcription_failed' ELSE 'ai_failed' END, "
                "error = '进程中断，等待重试', ai_error = CASE WHEN transcript = '' THEN ai_error ELSE '进程中断，等待重试' END, updated_at = ? "
                "WHERE status = 'processing'",
                (now,),
            )
            self.connection.execute(
                "UPDATE transcription_runs SET status = 'failed', error = '转写进程中断，需重新执行', "
                "completed_at = ?, updated_at = ? WHERE status = 'running'",
                (now, now),
            )
            self.connection.commit()

        self._run_startup_task(task)

    def discover(self, recordings: List[RecordingObject]) -> int:
        discovered = 0
        now = _now()
        for item in recordings:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO transcription_jobs
                    (object_name, device_id, etag, size, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    item.object_name,
                    device_id_from_recording_object(item.object_name, self.recordings_prefix),
                    item.etag,
                    item.size,
                    now,
                    now,
                ),
            )
            discovered += max(0, cursor.rowcount)
        self.connection.commit()
        return discovered

    def mark_missing(self, recordings: List[RecordingObject]) -> int:
        current_keys = {(item.object_name, item.etag) for item in recordings}
        rows = self.connection.execute(
            "SELECT id, object_name, etag FROM transcription_jobs "
            "WHERE status IN ('pending', 'failed', 'transcription_failed')"
        ).fetchall()
        missing_ids = [
            int(row["id"])
            for row in rows
            if (str(row["object_name"]), str(row["etag"])) not in current_keys
        ]
        now = _now()
        for job_id in missing_ids:
            self.connection.execute(
                "UPDATE transcription_jobs SET status = 'missing', error = '云端录音已不存在', updated_at = ? "
                "WHERE id = ?",
                (now, job_id),
            )
        self.connection.commit()
        return len(missing_ids)

    def claim_next(self, max_attempts: int, retry_delay_seconds: float) -> Optional[TranscriptionJob]:
        """Legacy claim API retained for callers using the original status names."""
        retry_before = (datetime.now(timezone.utc) - timedelta(seconds=retry_delay_seconds)).isoformat(
            timespec="seconds"
        )
        row = self.connection.execute(
            """
            SELECT id, object_name, device_id, etag, status, attempts
            FROM transcription_jobs
            WHERE status = 'pending'
               OR (status = 'failed' AND attempts < ? AND updated_at <= ?)
            ORDER BY id
            LIMIT 1
            """,
            (max_attempts, retry_before),
        ).fetchone()
        if row is None:
            return None
        now = _now()
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'processing', attempts = attempts + 1, error = '', updated_at = ? "
            "WHERE id = ?",
            (now, row["id"]),
        )
        self.connection.commit()
        return TranscriptionJob(
            id=int(row["id"]),
            object_name=str(row["object_name"]),
            device_id=str(row["device_id"]),
            etag=str(row["etag"]),
            status="processing",
            attempts=int(row["attempts"]) + 1,
        )

    def claim_next_transcription(
        self, max_attempts: int, retry_delay_seconds: float
    ) -> Optional[TranscriptionJob]:
        retry_before = (datetime.now(timezone.utc) - timedelta(seconds=retry_delay_seconds)).isoformat(
            timespec="seconds"
        )
        row = self.connection.execute(
            """
            SELECT id, object_name, device_id, etag, status, attempts
            FROM transcription_jobs
            WHERE status = 'pending'
               OR (status = 'transcription_failed' AND attempts < ? AND updated_at <= ?)
            ORDER BY id
            LIMIT 1
            """,
            (max_attempts, retry_before),
        ).fetchone()
        if row is None:
            return None
        now = _now()
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'transcribing', attempts = attempts + 1, "
            "stage_started_at = ?, error = '', updated_at = ? WHERE id = ?",
            (now, now, row["id"]),
        )
        self.connection.commit()
        return TranscriptionJob(
            id=int(row["id"]),
            object_name=str(row["object_name"]),
            device_id=str(row["device_id"]),
            etag=str(row["etag"]),
            status="transcribing",
            attempts=int(row["attempts"]) + 1,
        )

    def save_transcript(
        self,
        job_id: int,
        transcript: str,
        note_object_name: str,
        language: str,
        transcription_model: str,
        duration_seconds: Optional[float],
        download_seconds: float,
        asr_seconds: float,
        transcription_model_id: str = "",
    ) -> None:
        now = _now()
        self.connection.execute(
            """
            UPDATE transcription_jobs
            SET status = 'note_pending', transcript = ?, filtered_text = '', optimized_text = '',
                note_object_name = ?, language = ?, transcription_model = ?, duration_seconds = ?,
                transcription_model_id = ?,
                download_seconds = ?, asr_seconds = ?, note_attempts = 0, ai_attempts = 0,
                ai_stage = '', ai_error = '', error = '', stage_started_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                transcript,
                note_object_name,
                language,
                transcription_model,
                duration_seconds,
                transcription_model_id[:200],
                max(0.0, download_seconds),
                max(0.0, asr_seconds),
                now,
                now,
                job_id,
            ),
        )
        self.connection.commit()

    def claim_next_note(
        self, max_attempts: int, retry_delay_seconds: float
    ) -> Optional[StoredTextJob]:
        retry_before = (datetime.now(timezone.utc) - timedelta(seconds=retry_delay_seconds)).isoformat(
            timespec="seconds"
        )
        row = self.connection.execute(
            """
            SELECT * FROM transcription_jobs
            WHERE status = 'note_pending'
               OR (status = 'note_failed' AND note_attempts < ? AND updated_at <= ?)
            ORDER BY id
            LIMIT 1
            """,
            (max_attempts, retry_before),
        ).fetchone()
        if row is None:
            return None
        now = _now()
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'note_processing', note_attempts = note_attempts + 1, "
            "stage_started_at = ?, error = '', updated_at = ? WHERE id = ?",
            (now, now, row["id"]),
        )
        self.connection.commit()
        return self._stored_text_job(row, ai_attempts=int(row["ai_attempts"]))

    def mark_note_ready(self, job_id: int, raw_note_seconds: float) -> None:
        now = _now()
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'ai_pending', raw_note_seconds = ?, "
            "ai_stage = 'waiting', error = '', stage_started_at = ?, updated_at = ? WHERE id = ?",
            (max(0.0, raw_note_seconds), now, now, job_id),
        )
        self.connection.commit()

    def fail_transcription(self, job_id: int, error: str) -> None:
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'transcription_failed', error = ?, updated_at = ? WHERE id = ?",
            (error[:2000], _now(), job_id),
        )
        self.connection.commit()

    def start_transcription_run(
        self,
        job_id: int,
        model_id: str,
        run_kind: str = "initial",
        source_run_id: Optional[int] = None,
    ) -> int:
        """Persist one ASR attempt before downloading audio or calling a model."""
        if run_kind not in {"initial", "replay"}:
            raise ValueError("未知的转写运行类型。")
        now = _now()
        cursor = self.connection.execute(
            """
            INSERT INTO transcription_runs
                (job_id, source_run_id, run_kind, model_id, status, attempts, started_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'running', 1, ?, ?, ?)
            """,
            (job_id, source_run_id, run_kind, model_id[:200], now, now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def complete_transcription_run(
        self,
        run_id: int,
        model_name: str,
        language: str,
        duration_seconds: Optional[float],
        download_seconds: float,
        asr_seconds: float,
        transcript: str = "",
    ) -> None:
        now = _now()
        self.connection.execute(
            """
            UPDATE transcription_runs
            SET status = 'completed', model_name = ?, language = ?, duration_seconds = ?, transcript = ?,
                download_seconds = ?, asr_seconds = ?, error = '', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                model_name[:200],
                language[:80],
                duration_seconds,
                transcript,
                max(0.0, download_seconds),
                max(0.0, asr_seconds),
                now,
                now,
                run_id,
            ),
        )
        self.connection.commit()

    def fail_transcription_run(
        self,
        run_id: int,
        error: str,
        download_seconds: float = 0.0,
        asr_seconds: float = 0.0,
    ) -> None:
        now = _now()
        self.connection.execute(
            """
            UPDATE transcription_runs
            SET status = 'failed', error = ?,
                download_seconds = ?, asr_seconds = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                error[:2000],
                max(0.0, download_seconds),
                max(0.0, asr_seconds),
                now,
                now,
                run_id,
            ),
        )
        self.connection.commit()

    def queue_transcription_replay(self, source_run_id: int) -> Optional[int]:
        """Queue a read-only replay of one successful historical ASR run."""
        row = self.connection.execute(
            """
            SELECT job_id, model_id, model_name
            FROM transcription_runs
            WHERE id = ? AND status = 'completed' AND model_id != ''
            """,
            (source_run_id,),
        ).fetchone()
        if row is None:
            return None
        active = self.connection.execute(
            """
            SELECT 1
            FROM transcription_runs
            WHERE source_run_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (source_run_id,),
        ).fetchone()
        if active is not None:
            return None
        now = _now()
        cursor = self.connection.execute(
            """
            INSERT INTO transcription_runs
                (job_id, source_run_id, run_kind, model_id, model_name, status, created_at, updated_at)
            VALUES (?, ?, 'replay', ?, ?, 'queued', ?, ?)
            """,
            (
                int(row["job_id"]),
                source_run_id,
                str(row["model_id"]),
                str(row["model_name"]),
                now,
                now,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def claim_next_transcription_replay(self) -> Optional[TranscriptionRun]:
        row = self.connection.execute(
            """
            SELECT runs.*, jobs.object_name, jobs.device_id
            FROM transcription_runs AS runs
            JOIN transcription_jobs AS jobs ON jobs.id = runs.job_id
            WHERE runs.status = 'queued'
            ORDER BY runs.id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        now = _now()
        cursor = self.connection.execute(
            """
            UPDATE transcription_runs
            SET status = 'running', attempts = attempts + 1, started_at = ?, error = '', updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (now, now, int(row["id"])),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            return None
        return self._transcription_run(row, status="running", attempts=int(row["attempts"]) + 1)

    def list_transcription_runs(
        self,
        job_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[sqlite3.Row]:
        query = """
            SELECT
                runs.*,
                jobs.object_name,
                jobs.device_id
            FROM transcription_runs AS runs
            JOIN transcription_jobs AS jobs ON jobs.id = runs.job_id
        """
        parameters: list[object] = []
        if job_id is not None:
            query += " WHERE runs.job_id = ?"
            parameters.append(job_id)
        query += " ORDER BY runs.id DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(max(1, int(limit)))
        return list(self.connection.execute(query, parameters))

    @staticmethod
    def _transcription_run(
        row: sqlite3.Row,
        status: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> TranscriptionRun:
        source = row["source_run_id"]
        return TranscriptionRun(
            id=int(row["id"]),
            job_id=int(row["job_id"]),
            object_name=str(row["object_name"]),
            device_id=str(row["device_id"]),
            source_run_id=int(source) if source is not None else None,
            run_kind=str(row["run_kind"]),
            model_id=str(row["model_id"]),
            model_name=str(row["model_name"]),
            transcript=str(row["transcript"]),
            status=status if status is not None else str(row["status"]),
            attempts=int(row["attempts"]) if attempts is None else attempts,
        )

    def fail_note(self, job_id: int, error: str) -> None:
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'note_failed', error = ?, updated_at = ? WHERE id = ?",
            (error[:2000], _now(), job_id),
        )
        self.connection.commit()

    def claim_next_ai(
        self, max_attempts: int, retry_delay_seconds: float
    ) -> Optional[StoredTextJob]:
        retry_before = (datetime.now(timezone.utc) - timedelta(seconds=retry_delay_seconds)).isoformat(
            timespec="seconds"
        )
        row = self.connection.execute(
            """
            SELECT * FROM transcription_jobs
            WHERE status = 'ai_pending'
               OR (status = 'ai_failed' AND ai_attempts < ? AND updated_at <= ?)
            ORDER BY id
            LIMIT 1
            """,
            (max_attempts, retry_before),
        ).fetchone()
        if row is None:
            return None
        next_stage = "filtering" if str(row["optimized_text"]) else "optimizing"
        now = _now()
        self.connection.execute(
            "UPDATE transcription_jobs SET status = ?, ai_stage = ?, ai_attempts = ai_attempts + 1, "
            "stage_started_at = ?, ai_error = '', error = '', updated_at = ? WHERE id = ?",
            (next_stage, next_stage, now, now, row["id"]),
        )
        self.connection.commit()
        return self._stored_text_job(row, ai_attempts=int(row["ai_attempts"]) + 1, ai_stage=next_stage)

    def save_optimized(self, job_id: int, optimized_text: str, optimize_seconds: float) -> None:
        now = _now()
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'filtering', ai_stage = 'filtering', optimized_text = ?, "
            "optimize_seconds = ?, stage_started_at = ?, updated_at = ? WHERE id = ?",
            (optimized_text, max(0.0, optimize_seconds), now, now, job_id),
        )
        self.connection.commit()

    def complete_ai(
        self,
        job_id: int,
        filtered_text: str,
        filter_seconds: float,
        final_note_seconds: float,
    ) -> None:
        self.connection.execute(
            """
            UPDATE transcription_jobs
            SET status = 'completed', filtered_text = ?, filter_seconds = ?,
                final_note_seconds = ?, ai_stage = 'completed', ai_error = '', error = '', updated_at = ?
            WHERE id = ?
            """,
            (
                filtered_text,
                max(0.0, filter_seconds),
                max(0.0, final_note_seconds),
                _now(),
                job_id,
            ),
        )
        self.connection.commit()

    def fail_ai(self, job_id: int, error: str) -> None:
        detail = error[:2000]
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'ai_failed', ai_error = ?, error = ?, updated_at = ? WHERE id = ?",
            (detail, detail, _now(), job_id),
        )
        self.connection.commit()

    def retry_ai(self, job_id: int) -> bool:
        now = _now()
        cursor = self.connection.execute(
            """
            UPDATE transcription_jobs
            SET status = 'ai_pending', ai_attempts = 0, ai_stage = 'waiting',
                ai_error = '', error = '', stage_started_at = ?, updated_at = ?
            WHERE id = ? AND status = 'ai_failed'
            """,
            (now, now, job_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _stored_text_job(
        row: sqlite3.Row, ai_attempts: Optional[int] = None, ai_stage: Optional[str] = None
    ) -> StoredTextJob:
        duration = row["duration_seconds"]
        return StoredTextJob(
            id=int(row["id"]),
            object_name=str(row["object_name"]),
            device_id=str(row["device_id"]),
            transcript=str(row["transcript"]),
            optimized_text=str(row["optimized_text"]),
            note_object_name=str(row["note_object_name"]),
            language=str(row["language"]),
            transcription_model=str(row["transcription_model"]),
            duration_seconds=float(duration) if duration is not None else None,
            ai_attempts=int(row["ai_attempts"]) if ai_attempts is None else ai_attempts,
            ai_stage=str(row["ai_stage"]) if ai_stage is None else ai_stage,
        )

    def complete(
        self, job_id: int, transcript: str, note_object_name: str, filtered_text: str = "",
        optimized_text: str = ""
    ) -> None:
        self.connection.execute(
            """
            UPDATE transcription_jobs
            SET status = 'completed', transcript = ?, filtered_text = ?, optimized_text = ?, note_object_name = ?,
                error = '', updated_at = ?
            WHERE id = ?
            """,
            (transcript, filtered_text, optimized_text, note_object_name, _now(), job_id),
        )
        self.connection.commit()

    def fail(self, job_id: int, error: str) -> None:
        self.connection.execute(
            "UPDATE transcription_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (error[:2000], _now(), job_id),
        )
        self.connection.commit()

    def status_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM transcription_jobs "
            "WHERE object_name NOT LIKE '%/live/%' GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def device_status_counts(self) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT device_id, status, COUNT(*) AS count
                FROM transcription_jobs
                WHERE object_name NOT LIKE '%/live/%'
                GROUP BY device_id, status
                ORDER BY device_id, status
                """
            )
        )

    def note_needs_sync(self, object_name: str, etag: str | None = None) -> bool:
        row = self.connection.execute(
            "SELECT etag, local_path FROM obsidian_sync WHERE object_name = ?",
            (object_name,),
        ).fetchone()
        if row is None:
            return True
        local_path = str(row["local_path"])
        if not local_path or not Path(local_path).is_file():
            return False
        return etag is not None and str(row["etag"]) != etag

    def has_job_for_object(self, object_name: str) -> bool:
        """权威链路是否已经为该录音对象建过任务（直播预览的让位条件）。"""
        if not object_name:
            return False
        row = self.connection.execute(
            "SELECT id FROM transcription_jobs WHERE object_name = ?",
            (object_name,),
        ).fetchone()
        return row is not None

    def is_note_synced(self, object_name: str) -> bool:
        if not object_name:
            return False
        row = self.connection.execute(
            "SELECT local_path FROM obsidian_sync WHERE object_name = ?",
            (object_name,),
        ).fetchone()
        return row is not None and Path(str(row["local_path"])).is_file()

    def is_note_synced_since(self, object_name: str, changed_at: str) -> bool:
        if not object_name:
            return False
        row = self.connection.execute(
            "SELECT synced_at, local_path FROM obsidian_sync WHERE object_name = ?",
            (object_name,),
        ).fetchone()
        return (
            row is not None
            and Path(str(row["local_path"])).is_file()
            and str(row["synced_at"]) >= changed_at
        )

    def mark_note_synced(self, object_name: str, etag: str, local_path: Path) -> None:
        self.connection.execute(
            """
            INSERT INTO obsidian_sync (object_name, etag, local_path, synced_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(object_name) DO UPDATE SET
                etag = excluded.etag,
                local_path = excluded.local_path,
                synced_at = excluded.synced_at
            """,
            (object_name, etag, str(local_path), _now()),
        )
        self.connection.commit()

    def create_sync_activation_code(
        self,
        code_hash: str,
        device_id: str,
        expires_at: str = "",
        label: str = "",
        replace_existing: bool = False,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO sync_activation_codes
                (code_hash, device_id, label, expires_at, replace_existing, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                code_hash,
                device_id,
                label[:200],
                expires_at,
                int(replace_existing),
                _now(),
            ),
        )
        self.connection.commit()

    def list_sync_activation_codes(self) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT
                    code_hash,
                    device_id,
                    label,
                    expires_at,
                    replace_existing,
                    redeemed_at,
                    revoked_at,
                    created_at
                FROM sync_activation_codes
                ORDER BY created_at DESC
                """
            )
        )

    def revoke_sync_activation_code(self, code_hash: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE sync_activation_codes
            SET revoked_at = ?
            WHERE code_hash = ? AND redeemed_at = '' AND revoked_at = ''
            """,
            (_now(), code_hash),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def revoke_pending_replacement_activation_codes_for_device(
        self,
        device_id: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            UPDATE sync_activation_codes
            SET revoked_at = ?
            WHERE device_id = ?
                AND replace_existing = 1
                AND redeemed_at = ''
                AND revoked_at = ''
            """,
            (_now(), device_id),
        )
        self.connection.commit()
        return max(0, cursor.rowcount)

    def redeem_sync_activation_code(self, code_hash: str) -> Optional[str]:
        now = _now()
        row = self.connection.execute(
            """
            SELECT device_id, expires_at, redeemed_at, revoked_at
            FROM sync_activation_codes
            WHERE code_hash = ?
            """,
            (code_hash,),
        ).fetchone()
        if row is None or str(row["redeemed_at"]) or str(row["revoked_at"]):
            return None
        expires_at = str(row["expires_at"])
        if expires_at and _is_expired(expires_at):
            return None
        cursor = self.connection.execute(
            """
            UPDATE sync_activation_codes
            SET redeemed_at = ?
            WHERE code_hash = ? AND redeemed_at = '' AND revoked_at = ''
            """,
            (now, code_hash),
        )
        self.connection.commit()
        return str(row["device_id"]) if cursor.rowcount == 1 else None

    def redeem_sync_activation_code_and_create_token(
        self,
        code_hash: str,
        token_hash: str,
        label: str = "",
    ) -> Optional[str]:
        now = _now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT device_id, expires_at, redeemed_at, revoked_at, replace_existing
                FROM sync_activation_codes
                WHERE code_hash = ?
                """,
                (code_hash,),
            ).fetchone()
            if row is None or str(row["redeemed_at"]) or str(row["revoked_at"]):
                self.connection.rollback()
                return None
            expires_at = str(row["expires_at"])
            if expires_at and _is_expired(expires_at):
                self.connection.rollback()
                return None
            cursor = self.connection.execute(
                """
                UPDATE sync_activation_codes
                SET redeemed_at = ?
                WHERE code_hash = ? AND redeemed_at = '' AND revoked_at = ''
                """,
                (now, code_hash),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return None
            device_id = str(row["device_id"])
            if bool(row["replace_existing"]):
                self.connection.execute(
                    """
                    UPDATE sync_tokens
                    SET revoked_at = ?
                    WHERE device_id = ? AND revoked_at = ''
                    """,
                    (now, device_id),
                )
            self.connection.execute(
                """
                INSERT INTO sync_tokens (token_hash, device_id, label, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, device_id, label[:200], now),
            )
            self.connection.commit()
            return device_id
        except Exception:
            self.connection.rollback()
            raise

    def create_sync_token(self, token_hash: str, device_id: str, label: str = "") -> None:
        self.connection.execute(
            """
            INSERT INTO sync_tokens (token_hash, device_id, label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, device_id, label[:200], _now()),
        )
        self.connection.commit()

    def list_sync_tokens(self) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT token_hash, device_id, label, created_at, last_used_at, revoked_at
                FROM sync_tokens
                ORDER BY created_at DESC
                """
            )
        )

    def list_active_sync_tokens_for_device(self, device_id: str) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT label, created_at, last_used_at
                FROM sync_tokens
                WHERE device_id = ? AND revoked_at = ''
                ORDER BY created_at DESC
                """,
                (device_id,),
            )
        )

    def authenticate_sync_token(self, token_hash: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT device_id, revoked_at FROM sync_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None or str(row["revoked_at"]):
            return None
        self.connection.execute(
            "UPDATE sync_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now(), token_hash),
        )
        self.connection.commit()
        return str(row["device_id"])

    def revoke_sync_token(self, token_hash: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE sync_tokens
            SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at = ''
            """,
            (_now(), token_hash),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def revoke_sync_tokens_for_device(self, device_id: str) -> int:
        cursor = self.connection.execute(
            """
            UPDATE sync_tokens
            SET revoked_at = ?
            WHERE device_id = ? AND revoked_at = ''
            """,
            (_now(), device_id),
        )
        self.connection.commit()
        return max(0, cursor.rowcount)

    def create_user_account(self, email: str, password_hash: str) -> sqlite3.Row:
        now = _now()
        cursor = self.connection.execute(
            """
            INSERT INTO user_accounts (email, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (email, password_hash, now, now),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT id, email, password_hash, created_at
            FROM user_accounts
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        if row is None:
            raise RuntimeError("创建账户后未找到账户。")
        return row

    def get_user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT id, email, password_hash, created_at
            FROM user_accounts
            WHERE email = ? COLLATE NOCASE
            """,
            (email,),
        ).fetchone()

    def get_user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT id, email, password_hash, created_at
            FROM user_accounts
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    def create_user_session(
        self,
        token_hash: str,
        user_id: int,
        expires_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO user_sessions (token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, user_id, _now(), expires_at),
        )
        self.connection.commit()

    def get_user_by_session(self, token_hash: str) -> Optional[sqlite3.Row]:
        row = self.connection.execute(
            """
            SELECT users.id, users.email, users.created_at, sessions.expires_at
            FROM user_sessions AS sessions
            JOIN user_accounts AS users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
                AND sessions.revoked_at = ''
            """,
            (token_hash,),
        ).fetchone()
        if row is None or _is_expired(str(row["expires_at"])):
            return None
        return row

    def revoke_user_session(self, token_hash: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at = ''
            """,
            (_now(), token_hash),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_user_password(self, user_id: int, password_hash: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE user_accounts
            SET password_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (password_hash, _now(), user_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def revoke_other_user_sessions(self, user_id: int, keep_token_hash: str) -> int:
        cursor = self.connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = ?
            WHERE user_id = ? AND token_hash != ? AND revoked_at = ''
            """,
            (_now(), user_id, keep_token_hash),
        )
        self.connection.commit()
        return max(0, cursor.rowcount)

    def user_recorder_device_id(self, user_id: int) -> str:
        row = self.connection.execute(
            """
            SELECT device_id
            FROM user_recorder_bindings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return str(row["device_id"]) if row is not None else ""

    def bind_user_recorder_device(self, user_id: int, device_id: str) -> str:
        existing = self.user_recorder_device_id(user_id)
        if existing:
            if existing != device_id:
                raise ValueError("一个账户只能绑定一台录音豆。")
            return existing
        try:
            now = _now()
            self.connection.execute(
                """
                INSERT INTO user_recorder_bindings
                    (user_id, device_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, device_id, now, now),
            )
            self.connection.commit()
            return device_id
        except sqlite3.IntegrityError as exc:
            raise ValueError("这台录音豆已绑定到其他账户。") from exc

    def list_user_accounts_with_recorder_binding(self) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT
                    users.id,
                    users.email,
                    users.created_at,
                    bindings.device_id,
                    bindings.created_at AS bound_at,
                    bindings.updated_at AS binding_updated_at
                FROM user_accounts AS users
                LEFT JOIN user_recorder_bindings AS bindings
                    ON bindings.user_id = users.id
                ORDER BY users.created_at DESC
                """
            )
        )

    def list_managed_devices(self) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT device_id, label, created_at
                FROM managed_devices
                ORDER BY created_at ASC, device_id ASC
                """
            )
        )

    def register_managed_device(self, device_id: str, label: str = "") -> sqlite3.Row:
        try:
            self.connection.execute(
                """
                INSERT INTO managed_devices (device_id, label, created_at)
                VALUES (?, ?, ?)
                """,
                (device_id, label[:200], _now()),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("这台设备已经登记过了。") from exc
        row = self.connection.execute(
            """
            SELECT device_id, label, created_at
            FROM managed_devices
            WHERE device_id = ?
            """,
            (device_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("设备登记后读取失败。")
        return row

    def list_completed_notes(self, device_id: str) -> List[sqlite3.Row]:
        # "completed" 之外，凡已生成可读笔记的任务（转写完成、AI 润色中/失败）
        # 也进入同步列表：Obsidian 插件按 etag 增量拉取，可先看到流式转写文字，
        # AI 完成后内容自动更新为终版。
        return list(
            self.connection.execute(
                """
                SELECT current.id, current.note_object_name, current.updated_at
                FROM transcription_jobs AS current
                WHERE current.device_id = ? AND current.note_object_name != ''
                    AND (
                        current.status = 'completed'
                        OR current.status IN (
                            'note_pending', 'note_processing', 'ai_pending',
                            'filtering', 'ai_failed', 'note_failed',
                            'live_streaming'
                        )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM transcription_jobs AS newer
                        WHERE newer.device_id = current.device_id
                            AND (
                                newer.object_name = current.object_name
                                OR (
                                    newer.note_object_name != ''
                                    AND newer.note_object_name = current.note_object_name
                                )
                            )
                            AND newer.id > current.id
                    )
                ORDER BY current.id
                """,
                (device_id,),
            )
        )

    def get_completed_note(self, note_id: int, device_id: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT current.id, current.note_object_name, current.updated_at
            FROM transcription_jobs AS current
            WHERE current.id = ? AND current.device_id = ? AND current.note_object_name != ''
                AND (
                    current.status = 'completed'
                    OR current.status IN (
                        'note_pending', 'note_processing', 'ai_pending',
                        'filtering', 'ai_failed', 'note_failed',
                        'live_streaming'
                    )
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM transcription_jobs AS newer
                    WHERE newer.device_id = current.device_id
                        AND (
                            newer.object_name = current.object_name
                            OR (
                                newer.note_object_name != ''
                                AND newer.note_object_name = current.note_object_name
                            )
                        )
                        AND newer.id > current.id
                )
            """,
            (note_id, device_id),
        ).fetchone()

    def list_jobs(self) -> List[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM transcription_jobs "
                "WHERE object_name NOT LIKE '%/live/%' ORDER BY id"
            )
        )

    def has_live_stream_session(self, preview_object: str) -> bool:
        """流式引擎是否正在为该录音直播出字（scanner 据此跳过分段转写）。"""
        if not preview_object:
            return False
        row = self.connection.execute(
            "SELECT id FROM transcription_jobs "
            "WHERE object_name = ? AND status = 'live_streaming'",
            (preview_object,),
        ).fetchone()
        return row is not None

    def authoritative_note_completed(self, object_name: str) -> bool:
        """权威完整录音的笔记是否已完成（AI 终稿落定）。

        直播分段在此之后才可清理——过早删除会在权威转写出预览文字前
        丢掉分段（2026-11-18 实测教训：完整录音一上传就删分段，预览
        笔记从未生成）。
        """
        if not object_name:
            return False
        row = self.connection.execute(
            "SELECT id FROM transcription_jobs "
            "WHERE object_name = ? AND status = 'completed'",
            (object_name,),
        ).fetchone()
        return row is not None

    def upsert_live_preview_job(
        self,
        object_name: str,
        device_id: str,
        note_object_name: str,
    ) -> int:
        """为直播预览聚合笔记维护一个专用任务行（live/ 路径不参与常规发现）。

        权威完整录音上传后由 discover 建独立行，聚合行随后被
        list_completed_notes 的 NOT EXISTS 规则自然遮蔽。
        """
        now = _now()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO transcription_jobs
                (object_name, device_id, etag, size, status, created_at, updated_at)
            VALUES (?, ?, 'preview', 0, 'note_pending', ?, ?)
            """,
            (object_name, device_id, now, now),
        )
        row = self.connection.execute(
            "SELECT id FROM transcription_jobs WHERE object_name = ?",
            (object_name,),
        ).fetchone()
        if row is None:
            raise RuntimeError("直播预览任务行写入失败：%s" % object_name)
        return int(row["id"])

    def close(self) -> None:
        self.connection.close()


def _is_expired(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except ValueError:
        return True
