"""LAN 收音箱：接收录音豆直传的 WAV 并落盘归档。

设备端固件使用标准 S3 multipart 协议上传（SigV4 签名头会被忽略）：
1. POST <key>?uploads=            -> 返回 <UploadId>
2. PUT  <key>?partNumber=N&uploadId=X -> 200 + ETag
3. POST <key>?uploadId=X          -> 合并分片写入本地归档
4. DELETE <key>?uploadId=X        -> 丢弃会话（可选）

归档目录结构沿用 MinIO 对象名（recordings/<设备>/<文件>.wav），
scanner 通过 InboxArchiveStore 在本地归档与 MinIO 之间透明切换。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..config import ScannerSettings
from .contract import NoteObject, RecordingObject, RecordingStore

WAV_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+\.wav$", re.IGNORECASE)
PART_NUMBER_PATTERN = re.compile(r"<PartNumber>\s*(\d+)\s*</PartNumber>")
DEFAULT_RECEIVER_PORT = int(os.getenv("INBOX_RECEIVER_PORT", "8787"))
SESSION_TTL_SECONDS = 24 * 3600
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_DIRECT_BYTES = 4 * 1024 * 1024 * 1024


def is_receiver_endpoint(endpoint: str) -> bool:
    """判断设备云存储配置的 endpoint 是否指向本机接收器。

    命中时后端自身应忽略该配置、沿用本机存储，避免笔记链路被指到接收端口。
    """
    candidate = str(endpoint or "").strip()
    if not candidate:
        return False
    parsed = urlparse(candidate if "://" in candidate else "http://" + candidate)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    return port == DEFAULT_RECEIVER_PORT


def archive_root(settings: ScannerSettings) -> Path:
    return Path(settings.database_path).parent / "recordings_archive"


def is_live_segment_object(object_name: str, recordings_prefix: str = "recordings/") -> bool:
    """是否为直播预览分段对象（recordings/<设备>/live/<录音>/<分段>.wav）。"""
    rel = object_name.removeprefix(recordings_prefix)
    parts = Path(rel).parts
    return len(parts) >= 3 and parts[1] == "live"


def archive_file_path(settings: ScannerSettings, object_key: str) -> Path:
    relative = Path(object_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("录音对象路径无效。")
    return archive_root(settings) / relative


def object_key_from_request(path: str, settings: ScannerSettings) -> str:
    """从请求路径解析对象名，兼容 path-style（/bucket/key）与裸 key 两种形态。"""
    candidate = path.lstrip("/")
    bucket = settings.minio_bucket.strip("/")
    if bucket and candidate.startswith(bucket + "/"):
        candidate = candidate[len(bucket) + 1 :]
    prefix = settings.recordings_prefix.strip("/")
    if not prefix or not candidate.startswith(prefix + "/"):
        raise ValueError("录音上传路径无效：%s" % path)
    relative = Path(candidate)
    if ".." in relative.parts:
        raise ValueError("录音对象路径无效。")
    if not WAV_NAME_PATTERN.fullmatch(relative.name):
        raise ValueError("只接受 .wav 录音文件。")
    return relative.as_posix()


@dataclass
class _UploadSession:
    object_key: str
    directory: Path
    created_at: float


class InboxUploadSessions:
    """内存态 multipart 会话。

    接收进程重启后未完成的会话会丢失；设备端收到 404 会清除断点
    并自动重启整次上传（固件 upload loop 已实现该恢复路径）。
    """

    def __init__(self, settings: ScannerSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._sessions: Dict[str, _UploadSession] = {}
        self._staging_root = archive_root(self._settings) / ".staging"

    def _staging(self, upload_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", upload_id):
            raise ValueError("UploadId 无效。")
        return self._staging_root / upload_id

    def create(self, object_key: str) -> str:
        upload_id = uuid.uuid4().hex
        directory = self._staging(upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._collect_expired_locked()
            self._sessions[upload_id] = _UploadSession(object_key, directory, time.time())
        return upload_id

    def part_path(self, upload_id: str, part_number: int) -> Path:
        if part_number < 1 or part_number > 10000:
            raise ValueError("分片序号无效。")
        directory = self._staging(upload_id)
        if not directory.is_dir():
            raise LookupError("上传会话不存在或已过期。")
        return directory / ("part_%05d" % part_number)

    def complete(self, upload_id: str, part_numbers: List[int]) -> Tuple[str, int]:
        with self._lock:
            session = self._sessions.pop(upload_id, None)
        if session is None:
            raise LookupError("上传会话不存在或已过期。")
        try:
            final_path = archive_file_path(self._settings, session.object_key)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            temporary = final_path.with_name(final_path.name + ".incoming")
            with temporary.open("wb") as target:
                for number in sorted(part_numbers):
                    part = session.directory / ("part_%05d" % number)
                    if not part.is_file():
                        raise FileNotFoundError("缺少分片 %d。" % number)
                    with part.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            target.write(chunk)
                            total += len(chunk)
            temporary.replace(final_path)
        except Exception:
            # 会话已弹出但合并失败：保留分片目录以便排查，同时登记回会话表。
            with self._lock:
                self._sessions[upload_id] = session
            raise
        shutil.rmtree(session.directory, ignore_errors=True)
        return session.object_key, total

    def abort(self, upload_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(upload_id, None)
        if session is not None:
            shutil.rmtree(session.directory, ignore_errors=True)

    def _collect_expired_locked(self) -> None:
        now = time.time()
        expired = [
            upload_id
            for upload_id, session in self._sessions.items()
            if now - session.created_at > SESSION_TTL_SECONDS
        ]
        for upload_id in expired:
            session = self._sessions.pop(upload_id)
            shutil.rmtree(session.directory, ignore_errors=True)


_UPLOAD_SESSIONS: Optional[InboxUploadSessions] = None
_UPLOAD_SESSIONS_LOCK = threading.Lock()


def upload_sessions() -> InboxUploadSessions:
    global _UPLOAD_SESSIONS
    with _UPLOAD_SESSIONS_LOCK:
        if _UPLOAD_SESSIONS is None:
            _UPLOAD_SESSIONS = InboxUploadSessions(receiver_settings())
        return _UPLOAD_SESSIONS


_RECEIVER_SETTINGS: Optional[ScannerSettings] = None
_RECEIVER_SETTINGS_LOCK = threading.Lock()


def receiver_settings() -> ScannerSettings:
    """接收器使用的存储设定；始终基于本机默认配置，不读取手机端云存储配置。"""
    global _RECEIVER_SETTINGS
    with _RECEIVER_SETTINGS_LOCK:
        if _RECEIVER_SETTINGS is None:
            try:
                _RECEIVER_SETTINGS = ScannerSettings.load(use_saved_storage=False)
            except Exception:
                database_path = Path(
                    os.getenv(
                        "TRANSCRIPTION_DB_PATH",
                        str(
                            Path(__file__).resolve().parents[2]
                            / "data"
                            / "transcriptions.db"
                        ),
                    )
                ).expanduser()
                _RECEIVER_SETTINGS = ScannerSettings(
                    minio_endpoint="",
                    minio_access_key="",
                    minio_secret_key="",
                    minio_secure=False,
                    minio_bucket=os.getenv("STORAGE_BUCKET", "recordingbean"),
                    recordings_prefix="recordings/",
                    notes_prefix="notes/",
                    database_path=database_path,
                    scan_interval_seconds=15.0,
                    max_attempts=3,
                    retry_delay_seconds=60.0,
                    transcriber_factory="",
                )
        return _RECEIVER_SETTINGS


class InboxArchiveStore(RecordingStore):
    """录音走本地归档优先、云端兜底；笔记操作完全透传云端。"""

    def __init__(self, settings: ScannerSettings, base: RecordingStore) -> None:
        self._settings = settings
        self._base = base

    def list_recordings(self) -> List[RecordingObject]:
        merged: Dict[str, RecordingObject] = {}
        try:
            for item in self._base.list_recordings():
                if is_live_segment_object(
                    item.object_name, self._settings.recordings_prefix
                ):
                    continue
                merged[item.object_name] = item
        except Exception as exc:
            print("INBOX_CLOUD_LIST_FAIL %s" % exc, flush=True)
        root = archive_root(self._settings)
        if root.is_dir():
            for path in root.rglob("*.wav"):
                if ".staging" in path.parts:
                    continue
                object_name = path.relative_to(root).as_posix()
                if is_live_segment_object(
                    object_name, self._settings.recordings_prefix
                ):
                    continue
                stat = path.stat()
                merged[object_name] = RecordingObject(
                    object_name=object_name,
                    etag="%d-%d" % (stat.st_size, int(stat.st_mtime)),
                    size=stat.st_size,
                    last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
        return list(merged.values())

    def download_recording(self, object_name: str, destination: Path) -> None:
        source = archive_file_path(self._settings, object_name)
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            return
        self._base.download_recording(object_name, destination)

    def list_live_segments(self) -> List[RecordingObject]:
        merged: Dict[str, RecordingObject] = {}
        root = archive_root(self._settings)
        if root.is_dir():
            for path in root.rglob("*.wav"):
                if ".staging" in path.parts:
                    continue
                object_name = path.relative_to(root).as_posix()
                if not is_live_segment_object(
                    object_name, self._settings.recordings_prefix
                ):
                    continue
                stat = path.stat()
                merged[object_name] = RecordingObject(
                    object_name=object_name,
                    etag="%d-%d" % (stat.st_size, int(stat.st_mtime)),
                    size=stat.st_size,
                    last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
        lister = getattr(self._base, "list_live_segments", None)
        if callable(lister):
            for item in lister():
                merged[item.object_name] = item
        return list(merged.values())

    def delete_recording(self, object_name: str) -> None:
        source = archive_file_path(self._settings, object_name)
        if source.is_file():
            source.unlink()
        deleter = getattr(self._base, "delete_recording", None)
        if callable(deleter):
            deleter(object_name)

    def note_object_name(self, recording_object_name: str) -> str:
        return self._base.note_object_name(recording_object_name)

    def upload_note(self, object_name: str, markdown: str) -> None:
        self._base.upload_note(object_name, markdown)

    def list_notes(self, prefix: str | None = None) -> List[NoteObject]:
        return self._base.list_notes(prefix)

    def download_note(self, object_name: str) -> str:
        return self._base.download_note(object_name)
