"""Persisted, user-editable configurations for custom ASR providers."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping
from urllib.parse import urlparse


DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "name": "自定义 HTTP ASR",
    "description": "用户配置的 multipart HTTP 转写服务",
    "base_url": "",
    "health_path": "/health",
    "transcribe_path": "/transcribe",
    "timeout_seconds": 600,
    # LAN services often serve HTTPS with a self-signed certificate. Keep
    # verification on by default; set this to false only for a trusted network.
    "verify_tls": True,
    "file_field": "file",
    "language_field": "language",
    "prompt_field": "prompt",
    "response_text_path": "text",
    "response_language_path": "language",
    "response_model_path": "model",
    "response_segments_path": "segments",
    "api_key": "",
    "api_key_header": "Authorization",
    "api_key_prefix": "Bearer ",
    "headers_json": "",
}

_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LEGACY_PROVIDER_ID = "default"
FILE_VERSION = 2


def default_custom_asr_config_path() -> Path:
    value = os.getenv("CUSTOM_ASR_CONFIG_PATH", "").strip()
    return (
        Path(value).expanduser()
        if value
        else Path(__file__).resolve().parents[2] / "data" / "custom_asr.json"
    )


def load_custom_asr_configs(include_secret: bool = True) -> List[Dict[str, Any]]:
    configs: List[Dict[str, Any]] = []
    environment = _environment_config()
    if str(environment.get("base_url", "")).strip():
        normalized = _normalize(environment, "environment")
        factory = str(environment.get("factory", "")).strip()
        if factory:
            normalized["factory"] = factory
        configs.append(normalized)

    configs.extend(_load_saved_configs(include_secret=True))
    seen_ids = set()
    unique: List[Dict[str, Any]] = []
    for config in configs:
        provider_id = str(config.get("id", "")).strip()
        if not provider_id or provider_id in seen_ids:
            continue
        seen_ids.add(provider_id)
        unique.append(config)
    if not include_secret:
        return [_public_config(config) for config in unique]
    return unique


def load_custom_asr_config(include_secret: bool = True) -> Dict[str, Any]:
    """Backward-compatible view of the first configured custom provider."""
    configs = load_custom_asr_configs(include_secret=include_secret)
    if configs:
        return configs[0]
    config = dict(DEFAULTS)
    config.update(_environment_config())
    config["id"] = LEGACY_PROVIDER_ID
    return _public_config(config) if not include_secret else config


def public_custom_asr_configs() -> List[Dict[str, Any]]:
    return load_custom_asr_configs(include_secret=False)


def public_custom_asr_config() -> Dict[str, Any]:
    """Backward-compatible view used by older mobile builds."""
    configs = public_custom_asr_configs()
    if configs:
        return configs[0]
    return load_custom_asr_config(include_secret=False)


def save_custom_asr_config(payload: Mapping[str, Any]) -> Dict[str, Any]:
    provider_id = str(payload.get("id", "")).strip()
    if provider_id == "environment":
        raise ValueError("环境变量配置不能在手机端修改。")
    saved_configs = _load_saved_configs(include_secret=True)
    if provider_id:
        _provider_id(provider_id)
        current = next(
            (config for config in saved_configs if config["id"] == provider_id),
            None,
        )
        if current is None:
            raise ValueError("找不到要修改的自定义 ASR服务。")
    else:
        provider_id = _new_provider_id(saved_configs)
        current = {"id": provider_id}

    merged = dict(DEFAULTS)
    merged.update(current)
    for key in DEFAULTS:
        if key in payload:
            merged[key] = payload[key]
    merged["id"] = provider_id

    if "api_key" in payload and str(payload.get("api_key", "")).strip():
        merged["api_key"] = str(payload["api_key"]).strip()
    elif "clear_api_key" in payload and bool(payload["clear_api_key"]):
        merged["api_key"] = ""

    normalized = _normalize(merged, provider_id)
    replaced = False
    next_configs: List[Dict[str, Any]] = []
    for config in saved_configs:
        if config["id"] == provider_id:
            next_configs.append(normalized)
            replaced = True
        else:
            next_configs.append(config)
    if not replaced:
        next_configs.append(normalized)
    _write_saved_configs(next_configs)
    return _public_config(normalized)


def delete_custom_asr_config(provider_id: str) -> List[Dict[str, Any]]:
    provider_id = _provider_id(provider_id)
    saved_configs = _load_saved_configs(include_secret=True)
    remaining = [config for config in saved_configs if config["id"] != provider_id]
    if len(remaining) == len(saved_configs):
        raise ValueError("找不到要删除的自定义 ASR服务。")
    _write_saved_configs(remaining)
    return public_custom_asr_configs()


def _load_saved_configs(include_secret: bool) -> List[Dict[str, Any]]:
    path = default_custom_asr_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return []

    raw_configs: Any
    if isinstance(payload, dict) and isinstance(payload.get("providers"), list):
        raw_configs = payload["providers"]
    elif isinstance(payload, dict) and any(key in payload for key in DEFAULTS):
        # Migrate the previous single-provider object when it is next saved.
        raw_configs = [payload]
    else:
        raw_configs = []

    configs: List[Dict[str, Any]] = []
    for raw in raw_configs:
        if not isinstance(raw, dict):
            continue
        provider_id = str(raw.get("id", "")).strip() or LEGACY_PROVIDER_ID
        try:
            normalized = _normalize(raw, provider_id)
        except ValueError:
            continue
        configs.append(normalized if include_secret else _public_config(normalized))
    return configs


def _write_saved_configs(configs: List[Mapping[str, Any]]) -> None:
    path = default_custom_asr_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": FILE_VERSION,
        "providers": [dict(config) for config in configs],
    }
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


def _environment_config() -> Dict[str, Any]:
    mapping = {
        "enabled": ("CUSTOM_ASR_ENABLED", False),
        "name": ("CUSTOM_ASR_NAME", DEFAULTS["name"]),
        "description": ("CUSTOM_ASR_DESCRIPTION", DEFAULTS["description"]),
        "base_url": ("CUSTOM_ASR_BASE_URL", ""),
        "health_path": ("CUSTOM_ASR_HEALTH_PATH", DEFAULTS["health_path"]),
        "transcribe_path": ("CUSTOM_ASR_TRANSCRIBE_PATH", DEFAULTS["transcribe_path"]),
        "timeout_seconds": ("CUSTOM_ASR_TIMEOUT_SECONDS", DEFAULTS["timeout_seconds"]),
        "verify_tls": ("CUSTOM_ASR_VERIFY_TLS", DEFAULTS["verify_tls"]),
        "file_field": ("CUSTOM_ASR_FILE_FIELD", DEFAULTS["file_field"]),
        "language_field": ("CUSTOM_ASR_LANGUAGE_FIELD", DEFAULTS["language_field"]),
        "prompt_field": ("CUSTOM_ASR_PROMPT_FIELD", DEFAULTS["prompt_field"]),
        "response_text_path": ("CUSTOM_ASR_RESPONSE_TEXT_PATH", DEFAULTS["response_text_path"]),
        "response_language_path": (
            "CUSTOM_ASR_RESPONSE_LANGUAGE_PATH",
            DEFAULTS["response_language_path"],
        ),
        "response_model_path": (
            "CUSTOM_ASR_RESPONSE_MODEL_PATH",
            DEFAULTS["response_model_path"],
        ),
        "response_segments_path": (
            "CUSTOM_ASR_RESPONSE_SEGMENTS_PATH",
            DEFAULTS["response_segments_path"],
        ),
        "api_key": ("CUSTOM_ASR_API_KEY", ""),
        "api_key_header": ("CUSTOM_ASR_API_KEY_HEADER", DEFAULTS["api_key_header"]),
        "api_key_prefix": ("CUSTOM_ASR_API_KEY_PREFIX", DEFAULTS["api_key_prefix"]),
        "headers_json": ("CUSTOM_ASR_HEADERS_JSON", ""),
    }
    result: Dict[str, Any] = {}
    for key, (env_name, default) in mapping.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        if key == "enabled":
            result[key] = raw.strip().lower() in {"1", "true", "yes", "on"}
        elif key == "verify_tls":
            result[key] = raw.strip().lower() in {"1", "true", "yes", "on"}
        elif key == "timeout_seconds":
            try:
                result[key] = float(raw)
            except ValueError:
                result[key] = default
        else:
            result[key] = raw
    factory = os.getenv("CUSTOM_ASR_FACTORY")
    if factory is not None:
        result["factory"] = factory
    if (
        "CUSTOM_ASR_ENABLED" not in os.environ
        and str(result.get("base_url", "")).strip()
    ):
        result["enabled"] = True
    return result


def _normalize(config: Mapping[str, Any], provider_id: str) -> Dict[str, Any]:
    result = dict(DEFAULTS)
    result.update(config)
    result["id"] = _provider_id(provider_id)
    result["enabled"] = bool(result["enabled"])
    result["name"] = _text(result["name"], "name", 80)
    result["description"] = _text(result["description"], "description", 240)
    result["base_url"] = _url(result["base_url"])
    for key in (
        "health_path",
        "transcribe_path",
        "file_field",
        "language_field",
        "prompt_field",
        "response_text_path",
        "response_language_path",
        "response_model_path",
        "response_segments_path",
        "api_key_header",
    ):
        result[key] = _relative_or_empty(result[key], key)
    try:
        result["timeout_seconds"] = max(1.0, min(3600.0, float(result["timeout_seconds"])))
    except (TypeError, ValueError) as exc:
        raise ValueError("转写超时时间无效。") from exc
    result["verify_tls"] = _as_bool(result.get("verify_tls", True))
    result["api_key"] = str(result.get("api_key", "")).strip()
    result["api_key_prefix"] = str(result.get("api_key_prefix", ""))
    headers_json = str(result.get("headers_json", "")).strip()
    if headers_json:
        try:
            headers = json.loads(headers_json)
        except json.JSONDecodeError as exc:
            raise ValueError("自定义请求头必须是有效 JSON。") from exc
        if not isinstance(headers, dict):
            raise ValueError("自定义请求头必须是 JSON 对象。")
    result["headers_json"] = headers_json
    if result["enabled"] and not result["base_url"]:
        raise ValueError("启用自定义转写服务时必须填写服务地址。")
    return result


def _public_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    result = {key: config.get(key, default) for key, default in DEFAULTS.items()}
    result["id"] = str(config.get("id", LEGACY_PROVIDER_ID))
    result["api_key"] = ""
    result["api_key_configured"] = bool(str(config.get("api_key", "")).strip())
    return result


def _provider_id(value: Any) -> str:
    provider_id = str(value or "").strip().lower()
    if not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
        raise ValueError("自定义 ASR服务编号无效。")
    return provider_id


def _new_provider_id(configs: List[Mapping[str, Any]]) -> str:
    existing = {str(config.get("id", "")) for config in configs}
    while True:
        candidate = "asr-" + secrets.token_hex(5)
        if candidate not in existing:
            return candidate


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _text(value: Any, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError("%s 配置无效。" % field)
    return text


def _url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("自定义转写服务地址无效。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("自定义转写服务地址不能包含账号、参数或锚点。")
    return raw.rstrip("/")


def _relative_or_empty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if any(char in text for char in ("?", "#", " ", "\t", "\r", "\n")):
        raise ValueError("%s 配置无效。" % field)
    if text.startswith("http://") or text.startswith("https://") or ".." in text:
        raise ValueError("%s 必须是相对路径或字段名。" % field)
    return text
