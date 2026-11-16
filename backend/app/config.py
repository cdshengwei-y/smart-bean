"""Runtime configuration for the automatic transcription scanner."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from .cloud_storage_config import adapter_options, load_cloud_storage_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
HOST_MINIO_CONFIG = PROJECT_ROOT / "host_app" / "cloud" / "minio_config.json"


def _host_minio_config() -> Dict[str, Any]:
    config_path = Path(os.getenv("MINIO_CONFIG_PATH", str(HOST_MINIO_CONFIG))).expanduser()
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


@dataclass(frozen=True)
class ScannerSettings:
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_secure: bool
    minio_bucket: str
    recordings_prefix: str
    notes_prefix: str
    database_path: Path
    scan_interval_seconds: float
    max_attempts: int
    retry_delay_seconds: float
    transcriber_factory: str
    transcription_language: str
    obsidian_vault_path: Optional[Path] = None
    obsidian_notes_folder: str = "AI录音豆"
    obsidian_device_id: Optional[str] = None
    storage_factory: str = "app.storage.minio_store:create_store"
    minio_region: str = "us-east-1"
    minio_tls_ca_cert: str = ""
    storage_provider: str = "minio"
    storage_options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        storage_config: Optional[Mapping[str, Any]] = None,
        *,
        use_saved_storage: bool = True,
    ) -> "ScannerSettings":
        host_config = _host_minio_config()
        managed_config = (
            dict(storage_config)
            if storage_config is not None
            else (
                load_cloud_storage_config(include_secret=True)
                if use_saved_storage
                else None
            )
        )
        if managed_config and str(managed_config.get("type", "")) == "s3":
            from .storage.inbox import is_receiver_endpoint

            if is_receiver_endpoint(str(managed_config.get("endpoint", ""))):
                # 设备直传接收器模式：后端自身继续使用本机存储配置，
                # 笔记上传与历史录音扫描不受接收端口影响。
                managed_config = None
        storage_factory = (
            str(managed_config.get("adapter_factory", "")).strip()
            if managed_config and managed_config.get("type") == "custom"
            else os.getenv("STORAGE_FACTORY", "app.storage.minio_store:create_store").strip()
        )
        if not storage_factory:
            raise ValueError("STORAGE_FACTORY 不能为空。")

        raw_endpoint = (
            str(managed_config.get("endpoint", "")).strip()
            if managed_config
            else os.getenv("MINIO_ENDPOINT", str(host_config.get("endpoint", ""))).strip()
        )
        endpoint = ""
        secure = False
        if raw_endpoint:
            parsed = urlparse(raw_endpoint if "://" in raw_endpoint else "http://" + raw_endpoint)
            if not parsed.hostname:
                if storage_factory == "app.storage.minio_store:create_store":
                    raise ValueError("MINIO_ENDPOINT 格式无效。")
            else:
                endpoint = parsed.netloc
                secure = parsed.scheme == "https"

        access_key = (
            str(managed_config.get("access_key", "")).strip()
            if managed_config
            else os.getenv("MINIO_ACCESS_KEY", str(host_config.get("access_key", ""))).strip()
        )
        secret_key = (
            str(managed_config.get("secret_key", "")).strip()
            if managed_config
            else os.getenv("MINIO_SECRET_KEY", str(host_config.get("secret_key", ""))).strip()
        )
        if storage_factory == "app.storage.minio_store:create_store":
            if not endpoint:
                raise ValueError("未配置 MINIO_ENDPOINT，且未找到上位机 MinIO 配置。")
            if not access_key or not secret_key:
                raise ValueError("MinIO Access Key 和 Secret Key 不能为空。")

        database_path = Path(
            os.getenv("TRANSCRIPTION_DB_PATH", str(BACKEND_ROOT / "data" / "transcriptions.db"))
        ).expanduser()
        vault_path = _obsidian_vault_path()
        return cls(
            minio_endpoint=endpoint,
            minio_access_key=access_key,
            minio_secret_key=secret_key,
            minio_secure=secure,
            minio_bucket=(
                str(managed_config.get("bucket", "recordingbean")).strip()
                if managed_config
                else os.getenv(
                    "STORAGE_BUCKET",
                    os.getenv(
                        "MINIO_BUCKET",
                        str(host_config.get("bucket_name", "recordingbean")),
                    ),
                )
            ),
            recordings_prefix=_prefix(
                (
                    str(managed_config.get("recordings_prefix", "recordings/"))
                    if managed_config
                    else os.getenv(
                        "STORAGE_RECORDINGS_PREFIX",
                        os.getenv("MINIO_RECORDINGS_PREFIX", "recordings/"),
                    )
                )
            ),
            notes_prefix=_prefix(
                (
                    str(managed_config.get("notes_prefix", "notes/"))
                    if managed_config
                    else os.getenv(
                        "STORAGE_NOTES_PREFIX",
                        os.getenv("MINIO_NOTES_PREFIX", "notes/"),
                    )
                )
            ),
            database_path=database_path,
            scan_interval_seconds=max(1.0, float(os.getenv("SCAN_INTERVAL_SECONDS", "15"))),
            max_attempts=max(1, int(os.getenv("TRANSCRIPTION_MAX_ATTEMPTS", "3"))),
            retry_delay_seconds=max(1.0, float(os.getenv("TRANSCRIPTION_RETRY_DELAY_SECONDS", "60"))),
            transcriber_factory=os.getenv(
                "TRANSCRIBER_FACTORY", "app.transcription.model_registry:create_transcriber"
            ),
            transcription_language=os.getenv("TRANSCRIPTION_LANGUAGE", "zh"),
            obsidian_vault_path=vault_path,
            obsidian_notes_folder=os.getenv("OBSIDIAN_NOTES_FOLDER", "AI录音豆").strip().strip("/"),
            obsidian_device_id=_device_id(os.getenv("OBSIDIAN_DEVICE_ID", "")),
            storage_factory=storage_factory,
            minio_region=(
                str(managed_config.get("region", "us-east-1")).strip()
                if managed_config
                else os.getenv("MINIO_REGION", "us-east-1").strip()
            )
            or "us-east-1",
            minio_tls_ca_cert=(
                str(managed_config.get("tls_ca_cert", "")).strip()
                if managed_config
                else os.getenv("MINIO_TLS_CA_CERT", "").strip()
            ),
            storage_provider=(
                str(managed_config.get("provider", "custom")).strip()
                if managed_config
                else "minio"
            ),
            storage_options=adapter_options(managed_config)
            if managed_config and managed_config.get("type") == "custom"
            else {},
        )


def _prefix(value: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned:
        raise ValueError("MinIO 对象前缀不能为空。")
    return cleaned + "/"


def _device_id(value: str) -> Optional[str]:
    cleaned = value.strip()
    if not cleaned:
        return None
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in cleaned):
        raise ValueError("OBSIDIAN_DEVICE_ID 只能包含字母、数字、下划线和连字符。")
    return cleaned


def _obsidian_vault_path() -> Optional[Path]:
    """Enable direct local Vault writes only when an operator explicitly opts in."""
    configured = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_dir():
        raise ValueError("OBSIDIAN_VAULT_PATH 不存在：%s" % path)
    return path
