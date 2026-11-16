"""Persist and resolve the ASR model selected from the Android controller."""

from __future__ import annotations

import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, List, Optional, Tuple

from .contract import Transcriber, TranscriberError, TranscriptionRequest, TranscriptionResult
from .custom_config import (
    LEGACY_PROVIDER_ID,
    delete_custom_asr_config,
    load_custom_asr_configs,
    public_custom_asr_config,
    public_custom_asr_configs,
    save_custom_asr_config,
)
from .custom_http_adapter import CustomHttpTranscriber
from .loader import load_transcriber
from .local_model_adapter import LocalModelTranscriber


ZHIPU_GLM_ASR = "zhipu-glm-asr"
CUSTOM_ASR = "custom-asr"
CUSTOM_ASR_PREFIX = CUSTOM_ASR + ":"
LEGACY_QWEN_MODEL_ID = "Qwen3-ASR-1.7B"

_HEALTH_CACHE: Dict[str, Tuple[float, bool]] = {}
_HEALTH_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class AsrModel:
    id: str
    name: str
    description: str
    configured: bool


def default_model_config_path() -> Path:
    value = os.getenv("TRANSCRIPTION_MODEL_CONFIG_PATH", "").strip()
    return Path(value).expanduser() if value else Path(__file__).resolve().parents[2] / "data" / "asr_model.json"


class ModelRegistry:
    def __init__(
        self,
        config_path: Optional[Path] = None,
        health_checker: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.config_path = (config_path or default_model_config_path()).expanduser()
        self.health_checker = health_checker

    def models(self) -> List[AsrModel]:
        models = [
            AsrModel(
                id=ZHIPU_GLM_ASR,
                name="智谱 GLM-ASR",
                description="当前局域网 GLM-ASR 服务",
                configured=bool(os.getenv("ASR_BASE_URL", "http://192.168.0.153:10086").strip()),
            ),
        ]
        for config in self._custom_asr_configs():
            if not self._custom_asr_enabled(config):
                continue
            models.append(
                AsrModel(
                    id=self._custom_model_id(str(config["id"])),
                    name=str(config.get("name", "自定义 HTTP ASR")).strip()
                    or "自定义 HTTP ASR",
                    description=str(
                        config.get(
                            "description",
                            "用户配置的 multipart HTTP 转写服务",
                        )
                    ).strip(),
                    configured=True,
                )
            )
        return models

    @staticmethod
    def _custom_asr_enabled(config: Dict[str, object]) -> bool:
        return bool(config.get("enabled")) and bool(str(config.get("base_url", "")).strip())

    @staticmethod
    def _custom_asr_configs() -> List[Dict[str, object]]:
        return [dict(config) for config in load_custom_asr_configs()]

    @staticmethod
    def _custom_model_id(provider_id: str) -> str:
        return CUSTOM_ASR_PREFIX + provider_id

    def _custom_config_for_model(self, model_id: str) -> Optional[Dict[str, object]]:
        # Historical runs stored Qwen's response model name before custom ASR
        # providers received stable IDs. The original singleton is now `default`.
        if model_id == LEGACY_QWEN_MODEL_ID:
            model_id = self._custom_model_id(LEGACY_PROVIDER_ID)
        if not model_id.startswith(CUSTOM_ASR_PREFIX):
            return None
        return next(
            (
                config
                for config in self._custom_asr_configs()
                if self._custom_model_id(str(config.get("id", ""))) == model_id
            ),
            None,
        )

    @staticmethod
    def _build_custom_transcriber(config: Dict[str, object]) -> Transcriber:
        factory = str(config.get("factory", "")).strip()
        if factory:
            return load_transcriber(factory)
        return CustomHttpTranscriber(config=config)

    @staticmethod
    def custom_asr_config() -> Dict[str, object]:
        return public_custom_asr_config()

    @staticmethod
    def custom_asr_configs() -> List[Dict[str, object]]:
        return public_custom_asr_configs()

    def save_custom_asr_config(self, payload: Dict[str, object], select: bool = False) -> Dict[str, object]:
        config = save_custom_asr_config(payload)
        model_id = self._custom_model_id(str(config["id"]))
        _HEALTH_CACHE.pop(model_id, None)
        if select and bool(config.get("enabled")):
            self.select(model_id)
        return config

    def delete_custom_asr_config(self, provider_id: str) -> List[Dict[str, object]]:
        model_id = self._custom_model_id(provider_id)
        configs = delete_custom_asr_config(provider_id)
        _HEALTH_CACHE.pop(model_id, None)
        return [dict(config) for config in configs]

    def selected_id(self) -> str:
        configured_ids = {model.id for model in self.models()}
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            selected = str(payload.get("selected_model_id", ""))
            if selected in configured_ids:
                return selected
            if selected == CUSTOM_ASR:
                custom_models = [
                    model.id for model in self.models() if model.id.startswith(CUSTOM_ASR_PREFIX)
                ]
                if custom_models:
                    return custom_models[0]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        default = os.getenv("TRANSCRIPTION_DEFAULT_MODEL", ZHIPU_GLM_ASR).strip()
        return default if default in configured_ids else ZHIPU_GLM_ASR

    def status(self, include_health: bool = False) -> Dict[str, object]:
        selected = self.selected_id()
        health_by_model = self.health_status() if include_health else {}
        return {
            "selected_model_id": selected,
            "custom_asr": self.custom_asr_config(),
            "custom_asrs": self.custom_asr_configs(),
            "models": [
                asdict(model)
                | {"selected": model.id == selected}
                | ({"healthy": health_by_model.get(model.id, False)} if include_health else {})
                for model in self.models()
            ],
        }

    def select(self, model_id: str, include_health: bool = False) -> Dict[str, object]:
        model = next((item for item in self.models() if item.id == model_id), None)
        if model is None:
            raise ValueError("不支持的转写模型：%s" % model_id)
        if not model.configured:
            raise ValueError("%s 尚未在后端配置，无法切换。" % model.name)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.config_path.parent), delete=False
        ) as temporary:
            json.dump({"selected_model_id": model.id}, temporary, ensure_ascii=False)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.config_path)
        return self.status(include_health=include_health)

    def health_status(self) -> Dict[str, bool]:
        models = self.models()
        if self.health_checker is not None:
            return {
                model.id: model.configured and bool(self.health_checker(model.id))
                for model in models
            }

        now = time.monotonic()
        cache_seconds = max(1.0, float(os.getenv("ASR_HEALTH_CACHE_SECONDS", "10")))
        failure_cache_seconds = max(1.0, float(os.getenv("ASR_HEALTH_FAILURE_CACHE_SECONDS", "3")))
        result: Dict[str, bool] = {}
        expired: List[AsrModel] = []
        with _HEALTH_CACHE_LOCK:
            for model in models:
                cached = _HEALTH_CACHE.get(model.id)
                if not model.configured:
                    result[model.id] = False
                    continue
                if cached is not None:
                    age = now - cached[0]
                    ttl = cache_seconds if cached[1] else failure_cache_seconds
                    if age < ttl:
                        result[model.id] = cached[1]
                        continue
                expired.append(model)

        if expired:
            with ThreadPoolExecutor(max_workers=len(expired)) as executor:
                futures = {
                    model.id: executor.submit(self._check_model_health, model.id)
                    for model in expired
                }
                checked = {model_id: future.result() for model_id, future in futures.items()}
            checked_at = time.monotonic()
            with _HEALTH_CACHE_LOCK:
                for model_id, healthy in checked.items():
                    _HEALTH_CACHE[model_id] = (checked_at, healthy)
                    result[model_id] = healthy
        return result

    def _check_model_health(self, model_id: str) -> bool:
        timeout = max(0.5, float(os.getenv("ASR_HEALTH_TIMEOUT_SECONDS", "2.5")))
        try:
            if model_id == ZHIPU_GLM_ASR:
                payload = LocalModelTranscriber(timeout_seconds=timeout).health()
            elif (config := self._custom_config_for_model(model_id)) is not None:
                transcriber = self._build_custom_transcriber(config)
                health = getattr(transcriber, "health", None)
                if not callable(health):
                    return True
                payload = health()
            else:
                return False
        except (TranscriberError, OSError, ValueError):
            return False
        if payload.get("ok") is False:
            return False
        return str(payload.get("status", "")).lower() not in {"error", "failed", "offline", "unhealthy"}

    def build_selected(self) -> Transcriber:
        return self.build_model(self.selected_id())

    def build_model(self, model_id: str) -> Transcriber:
        """Build a concrete transcriber for a persisted model ID."""
        selected = str(model_id).strip()
        if selected == ZHIPU_GLM_ASR:
            return LocalModelTranscriber()
        if (config := self._custom_config_for_model(selected)) is not None:
            return self._build_custom_transcriber(config)
        raise TranscriberError("不支持的转写模型：%s" % selected)


class SelectedModelTranscriber:
    """Resolves the selection for each job, so a restart is not required."""

    provider_name = "selected-asr-model"

    def __init__(self, registry: Optional[ModelRegistry] = None) -> None:
        self.registry = registry or ModelRegistry()

    def health(self) -> Dict[str, object]:
        transcriber = self.registry.build_selected()
        health = getattr(transcriber, "health", None)
        return health() if callable(health) else {"ok": True}

    @property
    def selected_model_id(self) -> str:
        return self.registry.selected_id()

    def build_model(self, model_id: str) -> Transcriber:
        return self.registry.build_model(model_id)

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return self.registry.build_selected().transcribe(request)

    def transcribe_with_progress(
        self,
        request: TranscriptionRequest,
        on_progress: Callable[[TranscriptionResult], None] | None = None,
    ) -> TranscriptionResult:
        transcriber = self.registry.build_selected()
        progress_transcribe = getattr(transcriber, "transcribe_with_progress", None)
        if callable(progress_transcribe):
            return progress_transcribe(request, on_progress=on_progress)
        return transcriber.transcribe(request)


def create_transcriber() -> SelectedModelTranscriber:
    return SelectedModelTranscriber()
