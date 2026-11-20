#!/usr/bin/env python3
"""LAN status and ASR-model API for the Android controller."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape as xml_escape

from app.config import ScannerSettings
from app.cloud_storage_config import (
    clear_cloud_storage_config,
    cloud_storage_config_owner_id,
    load_cloud_storage_config,
    prepare_cloud_storage_config,
    public_cloud_storage_config,
    save_cloud_storage_config,
)
from app.database import JobRepository, ai_filter_enabled
from app.storage.loader import DEFAULT_STORAGE_FACTORY, load_recording_store
from app.storage.inbox import (
    PART_NUMBER_PATTERN,
    MAX_PART_BYTES,
    MAX_DIRECT_BYTES,
    archive_file_path,
    archive_root,
    is_receiver_endpoint,
    object_key_from_request,
    receiver_settings,
    upload_sessions,
)
from app.sync_api import CompletedNotesService, SyncAuthorizationService
from app.transcription.model_registry import ModelRegistry, default_model_config_path
from app.content_filter.profile import WorkProfileStore, DEFAULT_TYPES, RECORDING_SCENES


BACKEND_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = Path(os.getenv("TRANSCRIPTION_DB_PATH", str(BACKEND_ROOT / "data" / "transcriptions.db")))
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60
USER_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
ADMIN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ITERATIONS = 260_000
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _load_admin_password() -> str:
    password_file = os.getenv("SYNC_ADMIN_PASSWORD_FILE", "").strip()
    if password_file:
        try:
            return Path(password_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return os.getenv("SYNC_ADMIN_PASSWORD", "").strip()


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("请输入有效的邮箱地址。")
    return email


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "pbkdf2_sha256$%d$%s$%s" % (
        PASSWORD_ITERATIONS,
        salt.hex(),
        digest.hex(),
    )


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _uses_backend_default_storage(payload: Dict[str, Any]) -> bool:
    return payload.get("use_backend_default") is True


def _user_payload(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "email": str(row["email"]),
        "created_at": str(row["created_at"]),
    }


def _normalize_expiration(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "过期时间必须为 ISO 8601，例如 2026-09-01T00:00:00+08:00"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    if parsed <= datetime.now(timezone.utc):
        raise ValueError("过期时间必须晚于当前时间。")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _expiration_is_past(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except ValueError:
        return True


def _activation_status(row: Any) -> str:
    if str(row["revoked_at"]):
        return "revoked"
    if str(row["redeemed_at"]):
        return "redeemed"
    if _expiration_is_past(str(row["expires_at"])):
        return "expired"
    return "active"


def _admin_device_id_from_job(row: Any) -> str:
    """Fold pre-device rolling-recording sessions into the legacy device bucket."""
    device_id = str(row["device_id"]) if "device_id" in row.keys() else "legacy"
    object_name = str(row["object_name"])
    if (
        device_id.startswith("REC_")
        and object_name.startswith("recordings/%s/" % device_id)
    ):
        return "legacy"
    return device_id


def _transcription_run_payload(row: Any) -> Dict[str, Any]:
    transcript = str(row["transcript"] or "")
    return {
        "run_id": int(row["id"]),
        "job_id": int(row["job_id"]),
        "device_id": str(row["device_id"]),
        "recording_name": Path(str(row["object_name"])).name,
        "run_kind": str(row["run_kind"]),
        "source_run_id": int(row["source_run_id"]) if row["source_run_id"] is not None else None,
        "model_id": str(row["model_id"]),
        "model_name": str(row["model_name"]),
        "status": str(row["status"]),
        "attempts": int(row["attempts"]),
        "language": str(row["language"]),
        "duration_seconds": row["duration_seconds"],
        "asr_seconds": round(float(row["asr_seconds"]), 1),
        "error": str(row["error"]),
        "created_at": str(row["created_at"]),
        "started_at": str(row["started_at"]),
        "completed_at": str(row["completed_at"]),
        "transcript_preview": transcript[:240],
    }


def _admin_access_payload(repository: JobRepository) -> Dict[str, Any]:
    activation_codes = []
    for row in repository.list_sync_activation_codes():
        status = _activation_status(row)
        activation_codes.append(
            {
                "id": str(row["code_hash"]),
                "id_short": str(row["code_hash"])[:12],
                "device_id": str(row["device_id"]),
                "label": str(row["label"]),
                "expires_at": str(row["expires_at"]),
                "replaces_previous": bool(row["replace_existing"]),
                "redeemed_at": str(row["redeemed_at"]),
                "revoked_at": str(row["revoked_at"]),
                "created_at": str(row["created_at"]),
                "status": status,
            }
        )

    sync_tokens = []
    for row in repository.list_sync_tokens():
        status = "revoked" if str(row["revoked_at"]) else "active"
        sync_tokens.append(
            {
                "id": str(row["token_hash"]),
                "id_short": str(row["token_hash"])[:12],
                "device_id": str(row["device_id"]),
                "label": str(row["label"]),
                "created_at": str(row["created_at"]),
                "last_used_at": str(row["last_used_at"]),
                "revoked_at": str(row["revoked_at"]),
                "status": status,
            }
        )
    return {
        "activation_codes": activation_codes,
        "sync_tokens": sync_tokens,
    }


def _admin_dashboard_payload(
    repository: JobRepository,
    model_config_path: Path,
) -> Dict[str, Any]:
    access = _admin_access_payload(repository)
    users = []
    managed_device_rows = repository.list_managed_devices()
    managed_device_ids = tuple(str(row["device_id"]) for row in managed_device_rows)
    devices: Dict[str, Dict[str, Any]] = {}

    def new_device_summary(
        device_id: str,
        label: str = "",
        created_at: str = "",
    ) -> Dict[str, Any]:
        return {
            "device_id": device_id,
            "label": label,
            "created_at": created_at,
            "owner_email": "",
            "owner_user_id": None,
            "bound_at": "",
            "active_sync_clients": 0,
            "pending_activation_codes": 0,
            "jobs_total": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_in_progress": 0,
            "last_job_at": "",
            "last_job_status": "",
        }

    # Only explicitly managed devices belong to the formal directory.
    for row in managed_device_rows:
        device_id = str(row["device_id"])
        devices[device_id] = new_device_summary(
            device_id,
            str(row["label"]),
            str(row["created_at"]),
        )

    def device_summary(device_id: str) -> Optional[Dict[str, Any]]:
        if device_id not in devices:
            return None
        return devices[device_id]

    for row in repository.list_user_accounts_with_recorder_binding():
        device_id = str(row["device_id"] or "")
        users.append(
            {
                "id": int(row["id"]),
                "email": str(row["email"]),
                "created_at": str(row["created_at"]),
                "device_id": device_id,
                "bound_at": str(row["bound_at"] or ""),
            }
        )
        if device_id:
            summary = device_summary(device_id)
            if summary is not None:
                summary["owner_email"] = str(row["email"])
                summary["owner_user_id"] = int(row["id"])
                summary["bound_at"] = str(row["bound_at"] or "")

    for item in access["activation_codes"]:
        summary = device_summary(str(item["device_id"]))
        if summary is not None and item["status"] == "active":
            summary["pending_activation_codes"] += 1

    for item in access["sync_tokens"]:
        summary = device_summary(str(item["device_id"]))
        if summary is not None and item["status"] == "active":
            summary["active_sync_clients"] += 1

    recent_jobs = []
    for row in repository.list_jobs():
        device_id = _admin_device_id_from_job(row)
        summary = device_summary(device_id)
        status = str(row["status"])
        updated_at = str(row["updated_at"])
        if summary is not None:
            summary["jobs_total"] += 1
            if status == "completed":
                summary["jobs_completed"] += 1
            elif status == "missing":
                pass
            elif "failed" in status:
                summary["jobs_failed"] += 1
            else:
                summary["jobs_in_progress"] += 1
            if updated_at >= str(summary["last_job_at"]):
                summary["last_job_at"] = updated_at
                summary["last_job_status"] = status
        recent_jobs.append(
            {
                "id": int(row["id"]),
                "device_id": device_id,
                "recording_name": Path(str(row["object_name"])).name,
                "status": status,
                "updated_at": updated_at,
                "error": str(row["error"] or row["ai_error"]),
                "note_synced": repository.is_note_synced_since(
                    str(row["note_object_name"]),
                    updated_at,
                ),
            }
        )

    transcription_runs = [
        _transcription_run_payload(row)
        for row in repository.list_transcription_runs(limit=100)
    ]

    device_rows = [devices[device_id] for device_id in managed_device_ids]
    managed_device_id_set = set(managed_device_ids)
    recent_jobs.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    active_tokens = sum(
        1
        for item in access["sync_tokens"]
        if item["status"] == "active"
        and item["device_id"] in managed_device_id_set
    )
    active_codes = sum(
        1
        for item in access["activation_codes"]
        if item["status"] == "active"
        and item["device_id"] in managed_device_id_set
    )
    failed_jobs = sum(int(item["jobs_failed"]) for item in device_rows)
    in_progress_jobs = sum(int(item["jobs_in_progress"]) for item in device_rows)
    return {
        "summary": {
            "users": len(users),
            "bound_devices": sum(
                1 for item in users if item["device_id"] in managed_device_ids
            ),
            "known_devices": len(managed_device_ids),
            "active_sync_clients": active_tokens,
            "pending_activation_codes": active_codes,
            "jobs_total": sum(int(item["jobs_total"]) for item in device_rows),
            "jobs_failed": failed_jobs,
            "jobs_in_progress": in_progress_jobs,
        },
        "asr": ModelRegistry(model_config_path).status(include_health=True),
        "users": users,
        "devices": device_rows,
        "managed_devices": [
            {
                "device_id": str(row["device_id"]),
                "label": str(row["label"]),
                "created_at": str(row["created_at"]),
            }
            for row in managed_device_rows
        ],
        "recent_jobs": recent_jobs[:12],
        "transcription_runs": transcription_runs,
        "access": access,
    }


def _stage_elapsed(value: str) -> float:
    try:
        started = datetime.fromisoformat(value)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return round(max(0.0, (datetime.now(timezone.utc) - started).total_seconds()), 1)
    except (TypeError, ValueError):
        return 0.0


def status_payload(database_path: Path, model_config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return only operator-facing job metadata from the scanner database."""
    repository = JobRepository(database_path, ensure_schema=False)
    sync_path = database_path.with_name("obsidian_sync.db")
    sync_repository = JobRepository(sync_path, ensure_schema=False) if sync_path.is_file() else None
    try:
        jobs = []
        for row in repository.list_jobs():
            object_name = str(row["object_name"])
            device_id = str(row["device_id"]) if "device_id" in row.keys() else "legacy"
            note_object_name = str(row["note_object_name"])
            updated_at = str(row["updated_at"])
            durations = {
                "download": round(float(row["download_seconds"]), 1),
                "asr": round(float(row["asr_seconds"]), 1),
                "raw_note": round(float(row["raw_note_seconds"]), 1),
                "optimize": round(float(row["optimize_seconds"]), 1),
                "filter": round(float(row["filter_seconds"]), 1),
                "final_note": round(float(row["final_note_seconds"]), 1),
            }
            jobs.append(
                {
                    "job_id": int(row["id"]),
                    "device_id": device_id,
                    "recording_name": Path(object_name).name,
                    "status": str(row["status"]),
                    "attempts": int(row["attempts"]),
                    "ai_attempts": int(row["ai_attempts"]),
                    "ai_stage": str(row["ai_stage"]),
                    "stage_started_at": str(row["stage_started_at"]),
                    "stage_elapsed_seconds": _stage_elapsed(str(row["stage_started_at"])),
                    "updated_at": updated_at,
                    "error": str(row["error"]),
                    "ai_error": str(row["ai_error"]),
                    "transcription_model_id": str(
                        row["transcription_model_id"]
                        if "transcription_model_id" in row.keys()
                        else ""
                    ),
                    "transcription_model": str(
                        row["transcription_model"]
                        if "transcription_model" in row.keys()
                        else ""
                    ),
                    "note_synced": (
                        repository.is_note_synced_since(note_object_name, updated_at)
                        or (
                            sync_repository is not None
                            and sync_repository.is_note_synced_since(note_object_name, updated_at)
                        )
                    ),
                    "durations": durations,
                }
            )
        device_counts: Dict[str, Dict[str, int]] = {}
        count_by_device = getattr(repository, "device_status_counts", None)
        for row in count_by_device() if callable(count_by_device) else []:
            device_id = str(row["device_id"])
            device_counts.setdefault(device_id, {})[str(row["status"])] = int(row["count"])
        devices = [
            {"device_id": device_id, "counts": counts}
            for device_id, counts in device_counts.items()
        ]
        return {
            "counts": repository.status_counts(),
            "devices": devices,
            "jobs": jobs,
            "asr": ModelRegistry(model_config_path).status(include_health=True),
            "ai_filter_enabled": ai_filter_enabled(),
        }
    finally:
        if sync_repository is not None:
            sync_repository.close()
        repository.close()


class StatusHandler(BaseHTTPRequestHandler):
    database_path = DEFAULT_DATABASE
    model_config_path = default_model_config_path()
    profile_store = WorkProfileStore(BACKEND_ROOT / "data" / "work_profiles.json")
    sync_store_factory = None
    sync_notes_prefix = "notes/"
    admin_password = _load_admin_password()
    admin_static_root = BACKEND_ROOT / "admin_static"
    admin_sessions: Dict[str, float] = {}
    admin_session_lock = threading.Lock()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/admin", "/admin/"):
            self.serve_admin_file("index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/admin/static/"):
            relative_path = path[len("/admin/static/"):]
            self.serve_admin_file(relative_path, self._admin_content_type(relative_path))
            return
        if path == "/api/admin/session":
            self.admin_session_status()
            return
        if path == "/api/auth/me":
            self.auth_me()
            return
        if path == "/api/mobile/obsidian-sync":
            self.mobile_obsidian_sync_status()
            return
        mobile_sync_match = re.fullmatch(r"/api/mobile/obsidian-sync/([A-Za-z0-9_-]+)", path)
        if mobile_sync_match:
            self.mobile_obsidian_sync_status(mobile_sync_match.group(1))
            return
        if path == "/api/admin/access":
            if self.require_admin():
                self.admin_access()
            return
        if path == "/api/admin/dashboard":
            if self.require_admin():
                self.admin_dashboard()
            return
        if path == "/api/admin/transcription-runs":
            if self.require_admin():
                self.admin_transcription_runs()
            return
        if path == "/api/sync/notes":
            self.list_sync_notes()
            return
        note_match = re.fullmatch(r"/api/sync/notes/(\d+)", path)
        if note_match:
            self.download_sync_note(int(note_match.group(1)))
            return
        if path.startswith("/api/devices/") and path.endswith("/work-filter"):
            device_id = path[len("/api/devices/"):-len("/work-filter")].strip("/")
            self.send_json(HTTPStatus.OK, {"device_id": device_id, "profile": self.profile_store.get(device_id), "types": DEFAULT_TYPES, "scenes": RECORDING_SCENES})
            return
        if path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/live/frame":
            self.live_frame()
            return
        if path == "/api/status":
            try:
                self.send_json(HTTPStatus.OK, status_payload(self.database_path, self.model_config_path))
            except Exception as exc:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if path == "/api/config/asr":
            self.send_json(
                HTTPStatus.OK,
                {
                    "custom_asr": ModelRegistry(self.model_config_path).custom_asr_config(),
                    "custom_asrs": ModelRegistry(self.model_config_path).custom_asr_configs(),
                },
            )
            return
        if path == "/api/config/storage":
            self.cloud_storage_config()
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        if "uploads" in query:
            self._inbox_guard(self.inbox_create_multipart)
            return
        if "uploadId" in query:
            self._inbox_guard(self.inbox_complete_multipart)
            return
        path = urlsplit(self.path).path
        if path == "/live/stream":
            self.live_stream()
            return
        if path == "/api/auth/register":
            self.auth_register()
            return
        if path == "/api/auth/login":
            self.auth_login()
            return
        if path == "/api/auth/logout":
            self.auth_logout()
            return
        if path == "/api/auth/change-password":
            self.auth_change_password()
            return
        if path == "/api/admin/login":
            self.admin_login()
            return
        if path == "/api/admin/logout":
            self.admin_logout()
            return
        if path == "/api/admin/devices":
            if self.require_admin():
                self.admin_register_device()
            return
        if path == "/api/admin/activation-codes":
            if self.require_admin():
                self.admin_issue_activation_code()
            return
        if path == "/api/admin/activation-codes/revoke":
            if self.require_admin():
                self.admin_revoke_activation_code()
            return
        if path == "/api/admin/tokens/revoke":
            if self.require_admin():
                self.admin_revoke_token()
            return
        if path == "/api/admin/devices/revoke":
            if self.require_admin():
                self.admin_revoke_device()
            return
        replay_match = re.fullmatch(r"/api/admin/transcription-runs/(\d+)/replay", path)
        if replay_match:
            if self.require_admin():
                self.admin_replay_transcription(int(replay_match.group(1)))
            return
        if path == "/api/sync/activate":
            self.activate_sync_client()
            return
        if path == "/api/mobile/recorder-binding":
            self.mobile_bind_recorder()
            return
        if path == "/api/mobile/obsidian-sync/activation":
            self.mobile_issue_obsidian_sync_activation()
            return
        if path.startswith("/api/devices/") and path.endswith("/work-filter"):
            device_id = path[len("/api/devices/"):-len("/work-filter")].strip("/")
            try:
                profile = json.loads(self.read_request_body().decode("utf-8"))
                saved = self.profile_store.put(device_id, profile)
                self.send_json(HTTPStatus.OK, {"device_id": device_id, "profile": saved, "types": DEFAULT_TYPES, "scenes": RECORDING_SCENES})
            except Exception as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path == "/api/config/asr":
            try:
                body = json.loads(self.read_request_body(maximum_bytes=20000).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("自定义转写配置必须是 JSON 对象。")
                select = bool(body.pop("select", False))
                registry = ModelRegistry(self.model_config_path)
                config = registry.save_custom_asr_config(body, select=select)
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "custom_asr": config,
                        "custom_asrs": registry.custom_asr_configs(),
                        "asr": registry.status(include_health=False),
                    },
                )
            except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if path == "/api/config/storage/test":
            self.test_cloud_storage_config()
            return
        if path == "/api/config/storage":
            self.save_cloud_storage_config()
            return
        retry_match = re.fullmatch(r"/api/jobs/(\d+)/retry-ai", path)
        if retry_match:
            repository = JobRepository(self.database_path)
            try:
                job_id = int(retry_match.group(1))
                if repository.retry_ai(job_id):
                    self.send_json(HTTPStatus.OK, {"job_id": job_id, "status": "ai_pending"})
                else:
                    self.send_json(
                        HTTPStatus.CONFLICT,
                        {"error": "只有AI处理失败的任务可以重试。"},
                    )
            finally:
                repository.close()
            return
        if path != "/api/models/select":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            body = self.read_request_body().decode("utf-8")
            model_id = str(json.loads(body).get("model_id", "")).strip()
            self.send_json(
                HTTPStatus.OK,
                {"asr": ModelRegistry(self.model_config_path).select(model_id, include_health=True)},
            )
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_PUT(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        if "partNumber" in query and "uploadId" in query:
            self._inbox_guard(self.inbox_upload_part)
            return
        self._inbox_guard(self.inbox_direct_put)

    def _inbox_guard(self, action) -> None:
        try:
            action()
        except LookupError as exc:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except FileNotFoundError as exc:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def send_xml(self, status: HTTPStatus, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body_to_file(self, destination: Path, maximum_bytes: int) -> str:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > maximum_bytes:
            raise ValueError("请求内容无效。")
        digest = hashlib.md5()
        written = 0
        with destination.open("wb") as target:
            remaining = content_length
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("请求内容不完整。")
                target.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                remaining -= len(chunk)
        if written != content_length:
            raise ValueError("请求内容不完整。")
        return digest.hexdigest()

    def live_stream(self) -> None:
        """录音豆固件直播 PCM 推送端点：body 为 16k 单声道 s16le 原始 PCM。"""
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        device_id = query.get("device", [""])[0].strip()
        stem = query.get("recording", [""])[0].strip()
        if not device_id or not stem:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "device/recording required"})
            return
        try:
            pcm = self.read_request_body(maximum_bytes=1024 * 1024)
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        from app import live_engine

        if not live_engine.is_available():
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "sherpa-onnx 流式模型不可用"},
            )
            return
        snapshot = live_engine.feed_pcm(device_id, stem, pcm)
        self.send_json(HTTPStatus.OK, snapshot)

    def live_frame(self) -> None:
        """当前直播会话快照（APP 实时字幕展示）。"""
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        device_id = query.get("device", [""])[0].strip()
        stem = query.get("recording", [""])[0].strip()
        from app import live_engine

        self.send_json(HTTPStatus.OK, live_engine.snapshot(device_id, stem))

    def inbox_create_multipart(self) -> None:
        settings = receiver_settings()
        object_key = object_key_from_request(urlsplit(self.path).path, settings)
        upload_id = upload_sessions().create(object_key)
        self.send_xml(
            HTTPStatus.OK,
            "<InitiateMultipartUploadResult><Bucket>%s</Bucket>"
            "<Key>%s</Key><UploadId>%s</UploadId></InitiateMultipartUploadResult>"
            % (xml_escape(settings.minio_bucket), xml_escape(object_key), upload_id),
        )

    def inbox_upload_part(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        part_number = int(query.get("partNumber", ["0"])[0])
        upload_id = query.get("uploadId", [""])[0]
        part_path = upload_sessions().part_path(upload_id, part_number)
        digest = self.read_body_to_file(part_path, maximum_bytes=MAX_PART_BYTES)
        self.send_response(HTTPStatus.OK)
        self.send_header("ETag", '"%s"' % digest)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def inbox_complete_multipart(self) -> None:
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        upload_id = query.get("uploadId", [""])[0]
        body = self.read_request_body(maximum_bytes=1024 * 1024).decode(
            "utf-8", errors="replace"
        )
        part_numbers = [int(value) for value in PART_NUMBER_PATTERN.findall(body)]
        if not part_numbers:
            raise ValueError("完成请求没有包含任何分片。")
        object_key, size = upload_sessions().complete(upload_id, part_numbers)
        print("INBOX_UPLOAD %s size=%d" % (object_key, size), flush=True)
        settings = receiver_settings()
        self.send_xml(
            HTTPStatus.OK,
            "<CompleteMultipartUploadResult><Bucket>%s</Bucket><Key>%s</Key>"
            "<Location>%s</Location></CompleteMultipartUploadResult>"
            % (
                xml_escape(settings.minio_bucket),
                xml_escape(object_key),
                xml_escape(object_key),
            ),
        )

    def inbox_abort_multipart(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        upload_id = query.get("uploadId", [""])[0]
        upload_sessions().abort(upload_id)
        self.send_json(HTTPStatus.OK, {"aborted": True})

    def inbox_direct_put(self) -> None:
        """单请求整文件上传，便于手工测试与脚本接入。"""
        settings = receiver_settings()
        object_key = object_key_from_request(urlsplit(self.path).path, settings)
        final_path = archive_file_path(settings, object_key)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = final_path.with_name(final_path.name + ".incoming")
        digest = self.read_body_to_file(temporary, maximum_bytes=MAX_DIRECT_BYTES)
        temporary.replace(final_path)
        print(
            "INBOX_UPLOAD %s size=%d"
            % (object_key, int(final_path.stat().st_size)),
            flush=True,
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("ETag", '"%s"' % digest)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self) -> None:
        if "uploadId" in parse_qs(urlsplit(self.path).query, keep_blank_values=True):
            self._inbox_guard(self.inbox_abort_multipart)
            return
        path = urlsplit(self.path).path
        custom_match = re.fullmatch(r"/api/config/asr/([a-z0-9][a-z0-9_-]{0,63})", path)
        if custom_match is None:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            registry = ModelRegistry(self.model_config_path)
            configs = registry.delete_custom_asr_config(custom_match.group(1))
            self.send_json(
                HTTPStatus.OK,
                {
                    "custom_asrs": configs,
                    "asr": registry.status(include_health=False),
                },
            )
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def serve_admin_file(self, relative_path: str, content_type: str) -> None:
        try:
            root = self.admin_static_root.resolve()
            target = (root / relative_path).resolve()
            target.relative_to(root)
            if not target.is_file():
                raise FileNotFoundError(relative_path)
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (FileNotFoundError, OSError, ValueError):
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "管理页面资源不存在。"})

    @staticmethod
    def _admin_content_type(relative_path: str) -> str:
        suffix = Path(relative_path).suffix.lower()
        return {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(suffix, "application/octet-stream")

    def auth_register(self) -> None:
        try:
            body = json.loads(self.read_request_body(maximum_bytes=20000).decode("utf-8"))
            email = _normalize_email(str(body.get("email", "")))
            password = str(body.get("password", ""))
            if len(password) < 8:
                raise ValueError("密码至少需要 8 位。")
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        repository = JobRepository(self.database_path)
        try:
            if repository.get_user_by_email(email) is not None:
                self.send_json(HTTPStatus.CONFLICT, {"error": "这个邮箱已经注册。"})
                return
            try:
                user = repository.create_user_account(email, _hash_password(password))
            except sqlite3.IntegrityError:
                self.send_json(HTTPStatus.CONFLICT, {"error": "这个邮箱已经注册。"})
                return
            token = self._create_user_session(repository, int(user["id"]))
            self.send_json(
                HTTPStatus.CREATED,
                {"token": token, "user": _user_payload(user)},
            )
        finally:
            repository.close()

    def auth_login(self) -> None:
        try:
            body = json.loads(self.read_request_body(maximum_bytes=20000).decode("utf-8"))
            email = _normalize_email(str(body.get("email", "")))
            password = str(body.get("password", ""))
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        repository = JobRepository(self.database_path)
        try:
            user = repository.get_user_by_email(email)
            if user is None or not _verify_password(password, str(user["password_hash"])):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "邮箱或密码错误。"})
                return
            token = self._create_user_session(repository, int(user["id"]))
            self.send_json(
                HTTPStatus.OK,
                {"token": token, "user": _user_payload(user)},
            )
        finally:
            repository.close()

    def auth_me(self) -> None:
        token = self.user_bearer_token()
        if not token:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return
        repository = JobRepository(self.database_path)
        try:
            user = repository.get_user_by_session(_hash_secret(token))
            if user is None:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "登录已失效，请重新登录。"})
                return
            self.send_json(HTTPStatus.OK, {"user": _user_payload(user)})
        finally:
            repository.close()

    def auth_logout(self) -> None:
        token = self.user_bearer_token()
        if token:
            repository = JobRepository(self.database_path)
            try:
                repository.revoke_user_session(_hash_secret(token))
            finally:
                repository.close()
        self.send_json(HTTPStatus.OK, {"authenticated": False})

    def auth_change_password(self) -> None:
        token = self.user_bearer_token()
        if not token:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return
        try:
            body = json.loads(self.read_request_body(maximum_bytes=20000).decode("utf-8"))
            current_password = str(body.get("current_password", ""))
            new_password = str(body.get("new_password", ""))
            if len(new_password) < 8:
                raise ValueError("新密码至少需要 8 位。")
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        token_hash = _hash_secret(token)
        repository = JobRepository(self.database_path)
        try:
            session_user = repository.get_user_by_session(token_hash)
            if session_user is None:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "登录已失效，请重新登录。"})
                return
            user = repository.get_user_by_id(int(session_user["id"]))
            if user is None:
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "登录已失效，请重新登录。"})
                return
            if not _verify_password(current_password, str(user["password_hash"])):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "当前密码错误。"})
                return
            if not repository.update_user_password(
                int(user["id"]),
                _hash_password(new_password),
            ):
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "修改密码失败，请稍后重试。"},
                )
                return
            repository.revoke_other_user_sessions(int(user["id"]), token_hash)
            self.send_json(HTTPStatus.OK, {"changed": True})
        finally:
            repository.close()

    def _create_user_session(self, repository: JobRepository, user_id: int) -> str:
        token = secrets.token_urlsafe(48)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=USER_SESSION_TTL_SECONDS)
        ).isoformat(timespec="seconds")
        repository.create_user_session(_hash_secret(token), user_id, expires_at)
        return token

    def user_bearer_token(self) -> Optional[str]:
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return None
        cleaned = token.strip()
        return cleaned if cleaned else None

    def require_user(self) -> bool:
        token = self.user_bearer_token()
        if not token:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return False
        repository = JobRepository(self.database_path)
        try:
            user = repository.get_user_by_session(_hash_secret(token))
        finally:
            repository.close()
        if user is None:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "登录已失效，请重新登录。"})
            return False
        return True

    def admin_session_status(self) -> None:
        self.send_json(
            HTTPStatus.OK,
            {
                "configured": bool(self.admin_password),
                "authenticated": self.admin_session_token() is not None,
            },
        )

    def admin_login(self) -> None:
        if not self.admin_password:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "未配置 SYNC_ADMIN_PASSWORD 或 SYNC_ADMIN_PASSWORD_FILE。"},
            )
            return
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            password = str(body.get("password", ""))
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if not secrets.compare_digest(password, self.admin_password):
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "管理员密码错误。"})
            return

        token = secrets.token_urlsafe(32)
        handler_type = type(self)
        with handler_type.admin_session_lock:
            now = time.monotonic()
            handler_type.admin_sessions = {
                key: created
                for key, created in handler_type.admin_sessions.items()
                if now - created < ADMIN_SESSION_TTL_SECONDS
            }
            handler_type.admin_sessions[token] = now
        self.send_json(
            HTTPStatus.OK,
            {"authenticated": True},
            headers={
                "Set-Cookie": (
                    "admin_session=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict"
                    % (token, ADMIN_SESSION_TTL_SECONDS)
                )
            },
        )

    def admin_logout(self) -> None:
        token = self.admin_session_token()
        if token:
            handler_type = type(self)
            with handler_type.admin_session_lock:
                handler_type.admin_sessions.pop(token, None)
        self.send_json(
            HTTPStatus.OK,
            {"authenticated": False},
            headers={"Set-Cookie": "admin_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"},
        )

    def admin_session_token(self) -> Optional[str]:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get("admin_session")
        if morsel is None:
            return None
        token = morsel.value.strip()
        if not token:
            return None
        handler_type = type(self)
        with handler_type.admin_session_lock:
            created = handler_type.admin_sessions.get(token)
            if created is None:
                return None
            if time.monotonic() - created >= ADMIN_SESSION_TTL_SECONDS:
                handler_type.admin_sessions.pop(token, None)
                return None
        return token

    def require_admin(self) -> bool:
        if not self.admin_password:
            self.send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "未配置管理员密码。"},
            )
            return False
        if self.admin_session_token() is None:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "请先登录管理员端。"})
            return False
        return True

    def admin_access(self) -> None:
        repository = JobRepository(self.database_path)
        try:
            self.send_json(HTTPStatus.OK, _admin_access_payload(repository))
        finally:
            repository.close()

    def admin_dashboard(self) -> None:
        repository = JobRepository(self.database_path)
        try:
            self.send_json(
                HTTPStatus.OK,
                _admin_dashboard_payload(repository, self.model_config_path),
            )
        finally:
            repository.close()

    def admin_transcription_runs(self) -> None:
        repository = JobRepository(self.database_path)
        try:
            self.send_json(
                HTTPStatus.OK,
                {
                    "runs": [
                        _transcription_run_payload(row)
                        for row in repository.list_transcription_runs(limit=200)
                    ]
                },
            )
        finally:
            repository.close()

    def admin_replay_transcription(self, run_id: int) -> None:
        repository = JobRepository(self.database_path)
        try:
            replay_id = repository.queue_transcription_replay(run_id)
            if replay_id is None:
                self.send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "该转写记录不可回放，或已有回放任务正在进行。"},
                )
                return
            self.send_json(
                HTTPStatus.ACCEPTED,
                {"source_run_id": run_id, "replay_run_id": replay_id, "status": "queued"},
            )
        finally:
            repository.close()

    def admin_register_device(self) -> None:
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            device_id = str(body.get("device_id", "")).strip()
            if not DEVICE_ID_PATTERN.fullmatch(device_id):
                raise ValueError("设备 ID 只能包含字母、数字、下划线和连字符。")
            if device_id == "legacy" or device_id.startswith("REC_"):
                raise ValueError("设备 ID 不能使用历史录音或旧版会话标识。")
            label = str(body.get("label", "")).strip()
            repository = JobRepository(self.database_path)
            try:
                row = repository.register_managed_device(device_id, label)
            finally:
                repository.close()
            self.send_json(
                HTTPStatus.CREATED,
                {
                    "device_id": str(row["device_id"]),
                    "label": str(row["label"]),
                    "created_at": str(row["created_at"]),
                },
            )
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def admin_issue_activation_code(self) -> None:
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            device_id = str(body.get("device_id", "")).strip()
            label = str(body.get("label", "")).strip()
            expires_at = _normalize_expiration(str(body.get("expires_at", "")))
            replace_existing = body.get("replace_existing") is True
            repository = JobRepository(self.database_path)
            try:
                managed_device_ids = {
                    str(row["device_id"]) for row in repository.list_managed_devices()
                }
                if device_id not in managed_device_ids:
                    raise ValueError("请先登记这台设备，再签发激活码。")
                code = SyncAuthorizationService(repository).issue_activation_code(
                    device_id,
                    expires_at=expires_at,
                    label=label,
                    replace_existing=replace_existing,
                )
            finally:
                repository.close()
            self.send_json(
                HTTPStatus.CREATED,
                {
                    "device_id": device_id,
                    "activation_code": code,
                    "expires_at": expires_at,
                    "one_time": True,
                    "replaces_previous": replace_existing,
                },
            )
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def admin_revoke_activation_code(self) -> None:
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            code_id = str(body.get("code_id", "")).strip()
            if not ADMIN_HASH_PATTERN.fullmatch(code_id):
                raise ValueError("激活码记录 ID 无效。")
            repository = JobRepository(self.database_path)
            try:
                revoked = repository.revoke_sync_activation_code(code_id)
            finally:
                repository.close()
            if not revoked:
                self.send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "激活码不存在、已使用或已撤销。"},
                )
                return
            self.send_json(HTTPStatus.OK, {"revoked": True})
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def admin_revoke_token(self) -> None:
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            token_id = str(body.get("token_id", "")).strip()
            if not ADMIN_HASH_PATTERN.fullmatch(token_id):
                raise ValueError("同步令牌记录 ID 无效。")
            repository = JobRepository(self.database_path)
            try:
                revoked = repository.revoke_sync_token(token_id)
            finally:
                repository.close()
            if not revoked:
                self.send_json(HTTPStatus.CONFLICT, {"error": "令牌不存在或已撤销。"})
                return
            self.send_json(HTTPStatus.OK, {"revoked": True})
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def admin_revoke_device(self) -> None:
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            device_id = str(body.get("device_id", "")).strip()
            repository = JobRepository(self.database_path)
            try:
                revoked = SyncAuthorizationService(repository).revoke_device(device_id)
            finally:
                repository.close()
            self.send_json(HTTPStatus.OK, {"device_id": device_id, "revoked_tokens": revoked})
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def authenticated_user(self, repository: JobRepository) -> Optional[sqlite3.Row]:
        token = self.user_bearer_token()
        if not token:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return None
        user = repository.get_user_by_session(_hash_secret(token))
        if user is None:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "登录已失效，请重新登录。"})
        return user

    def cloud_storage_config(self) -> None:
        repository = JobRepository(self.database_path)
        try:
            user = self.authenticated_user(repository)
            if user is None:
                return
            owner_id = cloud_storage_config_owner_id()
            if owner_id is not None and owner_id != int(user["id"]):
                self.send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "云端存储已由另一个账户管理。"},
                )
                return
            saved = load_cloud_storage_config(include_secret=False)
            if saved is not None:
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "storage": {
                            **saved,
                            "source": "mobile",
                            "uses_backend_default": False,
                            "recorder_sync_supported": saved.get("type") == "s3",
                        }
                    },
                )
                return
            self.send_json(
                HTTPStatus.OK,
                {"storage": self.backend_default_storage_payload()},
            )
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        finally:
            repository.close()

    def save_cloud_storage_config(self) -> None:
        repository = JobRepository(self.database_path)
        try:
            user = self.authenticated_user(repository)
            if user is None:
                return
            owner_id = cloud_storage_config_owner_id()
            if owner_id is not None and owner_id != int(user["id"]):
                self.send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "云端存储已由另一个账户管理。"},
                )
                return
            body = json.loads(
                self.read_request_body(maximum_bytes=65536).decode("utf-8")
            )
            if not isinstance(body, dict):
                raise ValueError("云端存储配置必须是 JSON 对象。")
            if _uses_backend_default_storage(body):
                clear_cloud_storage_config()
                self.send_json(
                    HTTPStatus.OK,
                    {"storage": self.backend_default_storage_payload()},
                )
                return
            saved = save_cloud_storage_config(
                body,
                owner_user_id=int(user["id"]),
            )
            self.send_json(
                HTTPStatus.OK,
                {
                        "storage": {
                            **saved,
                            "source": "mobile",
                            "uses_backend_default": False,
                            "recorder_sync_supported": saved.get("type") == "s3",
                        }
                },
            )
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        finally:
            repository.close()

    def test_cloud_storage_config(self) -> None:
        repository = JobRepository(self.database_path)
        try:
            user = self.authenticated_user(repository)
            if user is None:
                return
            owner_id = cloud_storage_config_owner_id()
            if owner_id is not None and owner_id != int(user["id"]):
                self.send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "云端存储已由另一个账户管理。"},
                )
                return
            body = json.loads(
                self.read_request_body(maximum_bytes=65536).decode("utf-8")
            )
            if not isinstance(body, dict):
                raise ValueError("云端存储配置必须是 JSON 对象。")
            if _uses_backend_default_storage(body):
                settings = ScannerSettings.load(use_saved_storage=False)
                storage_payload = self.backend_default_storage_payload(settings)
            else:
                config = prepare_cloud_storage_config(body)
                settings = ScannerSettings.load(storage_config=config)
                storage_payload = {
                    **public_cloud_storage_config(config),
                    "source": "mobile",
                    "uses_backend_default": False,
                    "recorder_sync_supported": config.get("type") == "s3",
                }
            store = load_recording_store(settings)
            if is_receiver_endpoint(str(body.get("endpoint", ""))):
                # 接收器模式：endpoint 指向本机 8787，不做 MinIO 连通性测试。
                root = archive_root(receiver_settings())
                archived = len(list(root.rglob("*.wav"))) if root.is_dir() else 0
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "storage": storage_payload,
                        "recordings_count": archived,
                        "notes_count": len(store.list_notes()) if archived == 0 else 0,
                    },
                )
                return
            recordings = store.list_recordings()
            notes = store.list_notes()
            self.send_json(
                HTTPStatus.OK,
                {
                    "storage": storage_payload,
                    "recordings_count": len(recordings),
                    "notes_count": len(notes),
                },
            )
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
        finally:
            repository.close()

    def backend_default_storage_payload(
        self,
        settings: Optional[ScannerSettings] = None,
    ) -> Dict[str, Any]:
        settings = settings or ScannerSettings.load(use_saved_storage=False)
        storage_type = (
            "s3"
            if settings.storage_factory == DEFAULT_STORAGE_FACTORY
            else "custom"
        )
        legacy = {
            "type": storage_type,
            "provider": settings.storage_provider
            if storage_type == "s3"
            else "custom",
            "endpoint": (
                ("https://" if settings.minio_secure else "http://")
                + settings.minio_endpoint
            )
            if settings.minio_endpoint
            else "",
            "region": settings.minio_region,
            "bucket": settings.minio_bucket,
            "recordings_prefix": settings.recordings_prefix,
            "notes_prefix": settings.notes_prefix,
            "access_key": settings.minio_access_key,
            "secret_key": settings.minio_secret_key,
            "tls_ca_cert": settings.minio_tls_ca_cert,
            "adapter_factory": "" if storage_type == "s3" else settings.storage_factory,
            "adapter_options_json": "",
        }
        return {
            **public_cloud_storage_config(legacy),
            "source": "backend-default",
            "uses_backend_default": True,
            "recorder_sync_supported": storage_type == "s3",
        }

    def mobile_bind_recorder(self) -> None:
        try:
            body = json.loads(self.read_request_body(maximum_bytes=20000).decode("utf-8"))
            device_id = str(body.get("device_id", "")).strip()
            if not DEVICE_ID_PATTERN.fullmatch(device_id):
                raise ValueError("录音豆标识无效。")
            repository = JobRepository(self.database_path)
            try:
                user = self.authenticated_user(repository)
                if user is None:
                    return
                repository.bind_user_recorder_device(int(user["id"]), device_id)
            finally:
                repository.close()
            self.send_json(HTTPStatus.OK, {"bound": True})
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def mobile_obsidian_sync_status(self, legacy_device_id: str = "") -> None:
        repository = JobRepository(self.database_path)
        try:
            user = self.authenticated_user(repository)
            if user is None:
                return
            if legacy_device_id:
                device_id = repository.bind_user_recorder_device(
                    int(user["id"]), legacy_device_id
                )
            else:
                device_id = repository.user_recorder_device_id(int(user["id"]))
            if not device_id:
                self.send_json(HTTPStatus.OK, {"bound": False, "targets": []})
                return
            clients = SyncAuthorizationService(repository).active_clients(device_id)
            self.send_json(
                HTTPStatus.OK,
                {
                    "bound": True,
                    "targets": clients,
                },
            )
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            repository.close()

    def mobile_issue_obsidian_sync_activation(self) -> None:
        try:
            body = json.loads(self.read_request_body(maximum_bytes=20000).decode("utf-8"))
            label = str(body.get("target_name", "")).strip() or "新电脑"
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=15)
            ).isoformat(timespec="seconds")
            repository = JobRepository(self.database_path)
            try:
                user = self.authenticated_user(repository)
                if user is None:
                    return
                legacy_device_id = str(body.get("device_id", "")).strip()
                if legacy_device_id:
                    device_id = repository.bind_user_recorder_device(
                        int(user["id"]), legacy_device_id
                    )
                else:
                    device_id = repository.user_recorder_device_id(int(user["id"]))
                if not device_id:
                    self.send_json(
                        HTTPStatus.CONFLICT,
                        {"error": "录音豆尚未完成账户绑定。"},
                    )
                    return
                code = SyncAuthorizationService(repository).issue_activation_code(
                    device_id,
                    expires_at=expires_at,
                    label="手机切换：%s" % label,
                    replace_existing=True,
                )
            finally:
                repository.close()
            self.send_json(
                HTTPStatus.CREATED,
                {
                    "activation_code": code,
                    "expires_at": expires_at,
                    "one_time": True,
                    "replaces_previous": True,
                },
            )
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def activate_sync_client(self) -> None:
        try:
            body = json.loads(self.read_request_body().decode("utf-8"))
            code = str(body.get("activation_code", ""))
            label = str(body.get("client_label", ""))
            repository = JobRepository(self.database_path)
            try:
                device_id, token = SyncAuthorizationService(repository).activate(code, label)
            finally:
                repository.close()
            self.send_json(
                HTTPStatus.OK,
                {
                    "device_id": device_id,
                    "sync_token": token,
                    "token_type": "Bearer",
                },
            )
        except PermissionError as exc:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def list_sync_notes(self) -> None:
        repository: JobRepository | None = None
        try:
            device_id, service, repository = self.authenticated_notes_service()
            notes = service.list_notes(device_id)
            self.send_json(
                HTTPStatus.OK,
                {
                    "device_id": device_id,
                    "notes": [
                        {
                            "id": item.id,
                            "path": item.relative_path,
                            "etag": item.etag,
                            "updated_at": item.updated_at,
                        }
                        for item in notes
                    ],
                },
            )
        except PermissionError as exc:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        finally:
            if repository is not None:
                repository.close()

    def download_sync_note(self, note_id: int) -> None:
        repository: JobRepository | None = None
        try:
            device_id, service, repository = self.authenticated_notes_service()
            note, markdown = service.download_note(device_id, note_id)
            body = markdown.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"%s"' % note.etag)
            self.send_header("X-Recording-Bean-Path", note.relative_path)
            self.end_headers()
            self.wfile.write(body)
        except PermissionError as exc:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
        except FileNotFoundError as exc:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        finally:
            if repository is not None:
                repository.close()

    def authenticated_notes_service(
        self,
    ) -> tuple[str, CompletedNotesService, JobRepository]:
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise PermissionError("请提供同步令牌。")
        if self.sync_store_factory is None:
            settings = ScannerSettings.load()
            store = load_recording_store(settings)
            notes_prefix = settings.notes_prefix
        else:
            store = self.sync_store_factory()
            notes_prefix = self.sync_notes_prefix
        repository = JobRepository(self.database_path)
        try:
            device_id = SyncAuthorizationService(repository).authenticate(token)
            return (
                device_id,
                CompletedNotesService(
                    repository,
                    store,
                    notes_prefix,
                ),
                repository,
            )
        except Exception:
            repository.close()
            raise

    def read_request_body(self, maximum_bytes: int = 4096) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            total = 0
            while True:
                size_line = self.rfile.readline(128)
                if not size_line:
                    raise ValueError("请求内容不完整。")
                try:
                    size = int(size_line.split(b";", 1)[0].strip(), 16)
                except ValueError as exc:
                    raise ValueError("请求分块格式无效。") from exc
                if size == 0:
                    while self.rfile.readline(4096) not in (b"\r\n", b"\n", b""):
                        pass
                    break
                total += size
                if total > maximum_bytes:
                    raise ValueError("请求内容过大。")
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("请求内容不完整。")
                chunks.append(chunk)
            if not chunks:
                raise ValueError("请求内容无效。")
            return b"".join(chunks)

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > maximum_bytes:
            raise ValueError("请求内容无效。")
        return self.rfile.read(content_length)

    def send_json(
        self,
        status: HTTPStatus,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # A phone may abandon a polling request when its network changes.
            return

    def log_message(self, format: str, *args: object) -> None:
        print("status-api %s" % (format % args), flush=True)


def start_device_fetcher() -> None:
    """启动录音豆拉取器后台线程（从设备 TF 卡拉取录音与分段）。

    拉取式架构（2026-11-20）：设备不再自行上传，本服务轮询设备
    /files 拉新录音/分段到本地归档，scanner 归档发现链路自动转写。
    未配置 RECORDER_DEVICE_BASE_URL 时静默不启动。
    """
    from app import fetcher

    # 默认设备 mDNS 主机名（固件 startMdns 注册），可被 env 覆盖
    base_url = os.getenv(
        "RECORDER_DEVICE_BASE_URL", "http://ai-recorder-bean-ye-2.local"
    ).strip()
    if not base_url:
        print("Device fetcher disabled (empty base url)", flush=True)
        return
    device_id = os.getenv("RECORDER_DEVICE_ID", "bean_ye_2").strip() or "bean_ye_2"
    settings = ScannerSettings.load()
    fetcher_instance = fetcher.DeviceFetcher(
        settings,
        device_base_url=base_url,
        device_id=device_id,
        poll_interval_seconds=float(os.getenv("RECORDER_FETCH_INTERVAL", "10")),
    )
    threading.Thread(
        target=fetcher_instance.run_forever,
        name="device-fetcher",
        daemon=True,
    ).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve transcription status and ASR selection over the LAN")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE), type=Path)
    parser.add_argument("--model-config", default=str(default_model_config_path()), type=Path)
    args = parser.parse_args()

    StatusHandler.database_path = args.database.expanduser()
    StatusHandler.model_config_path = args.model_config.expanduser()
    StatusHandler.profile_store = WorkProfileStore(StatusHandler.database_path.with_name("work_profiles.json"))
    StatusHandler.admin_password = _load_admin_password()
    start_device_fetcher()
    server = ThreadingHTTPServer((args.host, args.port), StatusHandler)
    print("Transcription status API: http://%s:%d/api/status" % (args.host, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
