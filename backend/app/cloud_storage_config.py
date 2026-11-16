"""Persisted, user-editable cloud-storage configuration.

The recording backend is intentionally the only place that stores object-store
credentials. Mobile clients receive a masked view and the Obsidian plugin never
reads this file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse


DEFAULTS: Dict[str, Any] = {
    "type": "s3",
    "provider": "minio",
    "endpoint": "",
    "region": "us-east-1",
    "bucket": "recordingbean",
    "recordings_prefix": "recordings/",
    "notes_prefix": "notes/",
    "access_key": "",
    "secret_key": "",
    "tls_ca_cert": "",
    "adapter_factory": "",
    "adapter_options_json": "",
}

S3_PROVIDERS = {
    "minio",
    "aliyun-oss",
    "huawei-obs",
    "tencent-cos",
    "other-s3",
}
STORAGE_TYPES = {"s3", "custom"}
FACTORY_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
BUCKET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")
FILE_VERSION = 1


def default_cloud_storage_config_path() -> Path:
    value = os.getenv("CLOUD_STORAGE_CONFIG_PATH", "").strip()
    return (
        Path(value).expanduser()
        if value
        else Path(__file__).resolve().parents[1] / "data" / "cloud_storage.json"
    )


def load_cloud_storage_config(include_secret: bool = True) -> Optional[Dict[str, Any]]:
    path = default_cloud_storage_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        normalized = _normalize(raw)
    except ValueError:
        return None
    owner = _owner_id(raw.get("owner_user_id"))
    if owner is not None:
        normalized["owner_user_id"] = owner
    return normalized if include_secret else public_cloud_storage_config(normalized)


def cloud_storage_config_owner_id() -> Optional[int]:
    config = load_cloud_storage_config(include_secret=True)
    return _owner_id(config.get("owner_user_id")) if config else None


def clear_cloud_storage_config() -> bool:
    """Return to the backend-managed default storage configuration."""
    path = default_cloud_storage_config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def save_cloud_storage_config(
    payload: Mapping[str, Any],
    *,
    owner_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    current = load_cloud_storage_config(include_secret=True)
    merged = _merge_payload(payload, current)
    normalized = _normalize(merged)
    owner = _owner_id(owner_user_id)
    if owner is None and current is not None:
        owner = _owner_id(current.get("owner_user_id"))
    if owner is not None:
        normalized["owner_user_id"] = owner
    _write_config(normalized)
    return public_cloud_storage_config(normalized)


def prepare_cloud_storage_config(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a mobile payload without writing it to disk."""
    current = load_cloud_storage_config(include_secret=True)
    return _normalize(_merge_payload(payload, current))


def public_cloud_storage_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    result = {
        key: config.get(key, default)
        for key, default in DEFAULTS.items()
        if key
        not in {
            "access_key",
            "secret_key",
            "adapter_options_json",
        }
    }
    result["access_key"] = ""
    result["secret_key"] = ""
    result["adapter_options_json"] = ""
    result["access_key_configured"] = bool(str(config.get("access_key", "")).strip())
    result["secret_key_configured"] = bool(str(config.get("secret_key", "")).strip())
    result["adapter_options_configured"] = bool(
        str(config.get("adapter_options_json", "")).strip()
    )
    return result


def adapter_options(config: Mapping[str, Any]) -> Dict[str, Any]:
    text = str(config.get("adapter_options_json", "")).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("自定义适配器配置必须是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError("自定义适配器配置必须是 JSON 对象。")
    return value


def _merge_payload(
    payload: Mapping[str, Any],
    current: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    if current:
        merged.update(current)
    for key in DEFAULTS:
        if key not in payload:
            continue
        if key in {"access_key", "secret_key", "adapter_options_json"}:
            value = str(payload.get(key, ""))
            if value.strip():
                merged[key] = value
            continue
        merged[key] = payload[key]
    if bool(payload.get("clear_access_key")):
        merged["access_key"] = ""
    if bool(payload.get("clear_secret_key")):
        merged["secret_key"] = ""
    if bool(payload.get("clear_adapter_options")):
        merged["adapter_options_json"] = ""
    return merged


def _normalize(config: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(DEFAULTS)
    result.update(config)
    storage_type = str(result["type"]).strip().lower()
    if storage_type not in STORAGE_TYPES:
        raise ValueError("云端存储类型无效。")
    result["type"] = storage_type
    if storage_type == "s3":
        provider = str(result["provider"]).strip().lower()
        if provider not in S3_PROVIDERS:
            raise ValueError("S3 存储厂商无效。")
        result["provider"] = provider
        result["endpoint"] = _endpoint(result["endpoint"])
        result["region"] = _short_text(result["region"], "区域", 128)
        result["bucket"] = _bucket(result["bucket"])
        result["recordings_prefix"] = _prefix(result["recordings_prefix"], "录音路径")
        result["notes_prefix"] = _prefix(result["notes_prefix"], "笔记路径")
        result["access_key"] = _credential(result["access_key"], "Access Key")
        result["secret_key"] = _credential(result["secret_key"], "Secret Key")
        result["tls_ca_cert"] = _certificate(result["tls_ca_cert"])
        result["adapter_factory"] = ""
        result["adapter_options_json"] = ""
        return result

    result["provider"] = "custom"
    result["endpoint"] = _optional_endpoint(result["endpoint"])
    result["region"] = str(result["region"]).strip() or "us-east-1"
    result["bucket"] = str(result["bucket"]).strip() or "recordingbean"
    result["recordings_prefix"] = _prefix(result["recordings_prefix"], "录音路径")
    result["notes_prefix"] = _prefix(result["notes_prefix"], "笔记路径")
    factory = str(result["adapter_factory"]).strip()
    if not FACTORY_PATTERN.fullmatch(factory):
        raise ValueError("自定义适配器工厂格式应为 package.module:create_store。")
    result["adapter_factory"] = factory
    result["adapter_options_json"] = _options_json(result["adapter_options_json"])
    result["access_key"] = str(result["access_key"]).strip()
    result["secret_key"] = str(result["secret_key"]).strip()
    result["tls_ca_cert"] = _certificate(result["tls_ca_cert"])
    return result


def _endpoint(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请填写对象存储 Endpoint。")
    return _parse_endpoint(raw)


def _optional_endpoint(value: Any) -> str:
    raw = str(value or "").strip()
    return _parse_endpoint(raw) if raw else ""


def _parse_endpoint(raw: str) -> str:
    value = raw if "://" in raw else "https://" + raw
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("对象存储 Endpoint 无效。")
    return value.rstrip("/")


def _bucket(value: Any) -> str:
    bucket = str(value or "").strip()
    if not BUCKET_PATTERN.fullmatch(bucket):
        raise ValueError("Bucket 名称无效。")
    return bucket


def _prefix(value: Any, label: str) -> str:
    prefix = str(value or "").strip().strip("/")
    if (
        not prefix
        or len(prefix) > 512
        or ".." in prefix
        or any(character in prefix for character in "\\\r\n\t")
    ):
        raise ValueError("%s无效。" % label)
    return prefix + "/"


def _credential(value: Any, label: str) -> str:
    credential = str(value or "").strip()
    if not credential or len(credential) > 1024 or any(
        character in credential for character in "\r\n"
    ):
        raise ValueError("%s无效。" % label)
    return credential


def _certificate(value: Any) -> str:
    certificate = str(value or "").strip()
    if len(certificate) > 6000:
        raise ValueError("TLS CA 证书过长。")
    return certificate


def _short_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(character in text for character in "\r\n\t"):
        raise ValueError("%s无效。" % label)
    return text


def _options_json(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 12000:
        raise ValueError("自定义适配器配置过长。")
    if text:
        adapter_options({"adapter_options_json": text})
    return text


def _owner_id(value: Any) -> Optional[int]:
    try:
        owner = int(value)
    except (TypeError, ValueError):
        return None
    return owner if owner > 0 else None


def _write_config(config: Mapping[str, Any]) -> None:
    path = default_cloud_storage_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FILE_VERSION, **dict(config)}
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
