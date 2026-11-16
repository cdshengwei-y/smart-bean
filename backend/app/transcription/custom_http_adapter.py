"""Configurable multipart HTTP adapter for a customer-provided ASR service."""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from .contract import (
    TranscriberError,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionSegment,
)
from .custom_config import load_custom_asr_config


class CustomHttpTranscriber:
    """Call an ASR HTTP service that returns JSON with a configurable text path."""

    provider_name = "custom-asr"

    def __init__(
        self,
        base_url: Optional[str] = None,
        health_path: Optional[str] = None,
        transcribe_path: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        session: Optional[requests.Session] = None,
        config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        values = dict(config or load_custom_asr_config())
        self.base_url = (
            base_url if base_url is not None else str(values.get("base_url", ""))
        ).strip().rstrip("/")
        self.health_path = (
            health_path if health_path is not None else str(values.get("health_path", "/health"))
        )
        self.transcribe_path = transcribe_path or str(
            values.get("transcribe_path", "/transcribe")
        )
        self.timeout_seconds = float(
            timeout_seconds or values.get("timeout_seconds", 600)
        )
        self.health_timeout_seconds = max(
            0.5, float(os.getenv("CUSTOM_ASR_HEALTH_TIMEOUT_SECONDS", "2.5"))
        )
        self.file_field = str(values.get("file_field", "file")).strip() or "file"
        self.language_field = str(values.get("language_field", "language")).strip()
        self.prompt_field = str(values.get("prompt_field", "prompt")).strip()
        self.text_path = str(values.get("response_text_path", "text")).strip() or "text"
        self.language_path = str(values.get("response_language_path", "language")).strip()
        self.model_path = str(values.get("response_model_path", "model")).strip()
        self.segments_path = str(values.get("response_segments_path", "segments")).strip()
        self.api_key = str(values.get("api_key", "")).strip()
        self.api_key_header = str(values.get("api_key_header", "Authorization")).strip()
        self.api_key_prefix = str(values.get("api_key_prefix", "Bearer "))
        self.headers_json = str(values.get("headers_json", "")).strip()
        # LAN ASR services usually run HTTPS with a self-signed certificate, which
        # makes a plain-http request fail as "remote end closed connection".
        self.verify_tls = _as_bool(values.get("verify_tls", True))
        if not self.verify_tls:
            urllib3.disable_warnings(InsecureRequestWarning)
        self.session = session or requests.Session()
        self.session.verify = self.verify_tls

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise TranscriberError("未配置 CUSTOM_ASR_BASE_URL。")
        return "%s/%s" % (self.base_url, path.lstrip("/"))

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        raw_headers = self.headers_json
        if raw_headers:
            try:
                decoded = json.loads(raw_headers)
            except json.JSONDecodeError as exc:
                raise TranscriberError("CUSTOM_ASR_HEADERS_JSON 不是有效 JSON。") from exc
            if not isinstance(decoded, dict) or not all(
                isinstance(key, str) and isinstance(value, (str, int, float, bool))
                for key, value in decoded.items()
            ):
                raise TranscriberError("CUSTOM_ASR_HEADERS_JSON 必须是字符串键值对象。")
            headers.update({key: str(value) for key, value in decoded.items()})

        api_key = self.api_key
        if api_key:
            header = self.api_key_header
            prefix = self.api_key_prefix
            if not header:
                raise TranscriberError("CUSTOM_ASR_API_KEY_HEADER 不能为空。")
            headers[header] = prefix + api_key
        return headers

    def health(self) -> Dict[str, Any]:
        if not self.health_path.strip():
            return {"ok": True, "status": "not_checked"}
        try:
            response = self.session.get(
                self._url(self.health_path),
                headers=self._headers(),
                timeout=min(self.timeout_seconds, self.health_timeout_seconds),
            )
        except requests.RequestException as exc:
            raise TranscriberError("无法连接自定义 ASR 服务：%s" % exc) from exc
        if not response.ok:
            raise TranscriberError(
                "自定义 ASR 健康检查失败：HTTP %s %s"
                % (response.status_code, response.text[:300])
            )
        try:
            payload = response.json()
        except ValueError:
            return {"ok": True, "status": "ok"}
        if not isinstance(payload, dict):
            raise TranscriberError("自定义 ASR 健康检查返回格式无效。")
        return payload

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        audio_path = Path(request.audio_path)
        if not audio_path.is_file():
            raise TranscriberError("找不到待转写音频：%s" % audio_path)
        form: Dict[str, str] = {}
        if self.language_field and request.language:
            form[self.language_field] = request.language
        if self.prompt_field and request.prompt:
            form[self.prompt_field] = request.prompt
        try:
            with audio_path.open("rb") as audio_file:
                response = self.session.post(
                    self._url(self.transcribe_path),
                    files={
                        self.file_field: (
                            audio_path.name,
                            audio_file,
                            "audio/wav",
                        )
                    },
                    data=form or None,
                    headers=self._headers(),
                    timeout=self.timeout_seconds,
                )
        except requests.RequestException as exc:
            raise TranscriberError("自定义 ASR 转写请求失败：%s" % exc) from exc
        if not response.ok:
            raise TranscriberError(
                "自定义 ASR 转写失败：HTTP %s %s"
                % (response.status_code, response.text[:500])
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriberError("自定义 ASR 转写接口没有返回 JSON。") from exc
        if not isinstance(payload, dict):
            raise TranscriberError("自定义 ASR 转写接口返回格式无效。")

        text = self._string_at(payload, self.text_path)
        metadata: Dict[str, str] = {"service_url": self.base_url}
        if not text:
            metadata["empty_transcript"] = "true"
        return TranscriptionResult(
            text=text,
            language=self._string_at(payload, self.language_path) or request.language or "",
            duration_seconds=self._duration(audio_path),
            segments=self._segments_at(payload, self.segments_path),
            model_name=self._string_at(payload, self.model_path) or self.provider_name,
            metadata=metadata,
        )

    @staticmethod
    def _value_at(payload: Mapping[str, Any], path: str) -> Any:
        value: Any = payload
        if not path:
            return None
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                return None
            value = value[part]
        return value

    def _string_at(self, payload: Mapping[str, Any], path: str) -> str:
        value = self._value_at(payload, path)
        return str(value).strip() if value is not None else ""

    def _segments_at(
        self,
        payload: Mapping[str, Any],
        path: str,
    ) -> tuple[TranscriptionSegment, ...]:
        value = self._value_at(payload, path)
        if not isinstance(value, list):
            return ()
        segments = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            try:
                start = float(item.get("start_seconds", item.get("start", 0)))
                end = float(item.get("end_seconds", item.get("end", start)))
            except (TypeError, ValueError):
                continue
            segments.append(
                TranscriptionSegment(
                    start_seconds=max(0.0, start),
                    end_seconds=max(max(0.0, start), end),
                    text=text,
                    speaker=str(item.get("speaker", "")).strip(),
                )
            )
        return tuple(segments)

    @staticmethod
    def _duration(path: Path) -> Optional[float]:
        try:
            with wave.open(str(path), "rb") as audio:
                rate = audio.getframerate()
                return audio.getnframes() / rate if rate else None
        except (OSError, EOFError, wave.Error):
            return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def create_transcriber() -> CustomHttpTranscriber:
    return CustomHttpTranscriber(config=load_custom_asr_config())
