"""Adapter for the LAN Qwen3-ASR-1.7B service."""

from __future__ import annotations

import os
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

from .contract import TranscriberError, TranscriptionRequest, TranscriptionResult


class QwenAsrTranscriber:
    provider_name = "qwen3-asr-1.7b"

    def __init__(
        self,
        base_url: Optional[str] = None,
        health_path: Optional[str] = None,
        transcribe_path: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        chunk_seconds: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("QWEN_ASR_BASE_URL", "http://192.168.0.33:18005")
        ).rstrip("/")
        self.health_path = health_path or os.getenv("QWEN_ASR_HEALTH_PATH", "/health")
        self.transcribe_path = transcribe_path or os.getenv("QWEN_ASR_TRANSCRIBE_PATH", "/transcribe")
        self.timeout_seconds = float(timeout_seconds or os.getenv("QWEN_ASR_TIMEOUT_SECONDS", "600"))
        self.health_timeout_seconds = max(
            0.5, float(os.getenv("QWEN_ASR_HEALTH_TIMEOUT_SECONDS", "2.5"))
        )
        # This limits request size for multi-hour WAV recordings; the source WAV is never changed.
        self.chunk_seconds = max(1.0, float(chunk_seconds or os.getenv("QWEN_ASR_CHUNK_SECONDS", "15")))
        self.chunk_retries = max(1, int(os.getenv("QWEN_ASR_CHUNK_RETRIES", "3")))
        self.session = session or requests.Session()
        self.chunk_workers = max(1, min(4, int(os.getenv("QWEN_ASR_CHUNK_WORKERS", "2"))))

    def _url(self, path: str) -> str:
        return "%s/%s" % (self.base_url, path.lstrip("/"))

    def health(self) -> Dict[str, Any]:
        try:
            response = self.session.get(
                self._url(self.health_path),
                timeout=min(self.timeout_seconds, self.health_timeout_seconds),
            )
        except requests.RequestException as exc:
            raise TranscriberError("无法连接阿里 Qwen ASR 服务：%s" % exc) from exc
        if not response.ok:
            raise TranscriberError("阿里 Qwen ASR 健康检查失败：HTTP %s %s" % (response.status_code, response.text[:300]))
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriberError("阿里 Qwen ASR 健康检查没有返回 JSON。") from exc
        if not isinstance(payload, dict):
            raise TranscriberError("阿里 Qwen ASR 健康检查返回格式无效。")
        return payload

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return self.transcribe_with_progress(request)

    def transcribe_with_progress(
        self,
        request: TranscriptionRequest,
        on_progress: Callable[[TranscriptionResult], None] | None = None,
    ) -> TranscriptionResult:
        audio_path = Path(request.audio_path)
        if not audio_path.is_file():
            raise TranscriberError("找不到待转写音频：%s" % audio_path)
        duration_seconds = self._wav_duration(audio_path)
        if duration_seconds and duration_seconds > self.chunk_seconds:
            text, payload = self._transcribe_in_chunks(
                audio_path, request, duration_seconds, on_progress
            )
            chunked = True
        else:
            payload = self._transcribe_file(audio_path, request.language)
            text = self._payload_text(payload)
            chunked = False
        metadata = {"service_url": self.base_url}
        if chunked:
            metadata["chunked"] = "true"
            metadata["chunk_seconds"] = str(int(self.chunk_seconds))
        if not text:
            metadata["empty_transcript"] = "true"
        for key in ("model", "elapsed_seconds"):
            if payload.get(key) is not None:
                metadata[key] = str(payload[key])
        return TranscriptionResult(
            text=text,
            language=str(payload.get("language") or request.language or ""),
            duration_seconds=duration_seconds,
            model_name=str(payload.get("model") or self.provider_name),
            metadata=metadata,
        )

    def _transcribe_file(self, audio_path: Path, language: Optional[str]) -> Dict[str, Any]:
        try:
            with audio_path.open("rb") as audio_file:
                data = {"language": language} if language else None
                response = self.session.post(
                    self._url(self.transcribe_path),
                    files={"file": (audio_path.name, audio_file, "audio/wav")},
                    data=data,
                    timeout=self.timeout_seconds,
                )
        except requests.RequestException as exc:
            raise TranscriberError("阿里 Qwen ASR 转写请求失败：%s" % exc) from exc
        if not response.ok:
            raise TranscriberError("阿里 Qwen ASR 转写失败：HTTP %s %s" % (response.status_code, response.text[:500]))
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriberError("阿里 Qwen ASR 转写接口没有返回 JSON。") from exc
        if not isinstance(payload, dict):
            raise TranscriberError("阿里 Qwen ASR 转写接口返回格式无效。")
        return payload

    @staticmethod
    def _payload_text(payload: Dict[str, Any]) -> str:
        return str(payload.get("text", "")).strip()

    def _transcribe_in_chunks(
        self,
        audio_path: Path,
        request: TranscriptionRequest,
        duration_seconds: Optional[float],
        on_progress: Callable[[TranscriptionResult], None] | None = None,
    ) -> tuple[str, Dict[str, Any]]:
        try:
            with wave.open(str(audio_path), "rb") as source:
                frame_rate = source.getframerate()
                if frame_rate <= 0:
                    raise TranscriberError("WAV 采样率无效。")
                params = source.getparams()
                chunk_frames = max(1, int(frame_rate * self.chunk_seconds))
                chunk_paths = []
                last_payload: Dict[str, Any] = {}
                with tempfile.TemporaryDirectory(prefix="recording-bean-qwen-") as directory:
                    index = 0
                    while True:
                        frames = source.readframes(chunk_frames)
                        if not frames:
                            break
                        chunk_path = Path(directory) / ("chunk_%03d.wav" % index)
                        with wave.open(str(chunk_path), "wb") as chunk:
                            chunk.setparams(params)
                            chunk.writeframes(frames)
                        chunk_paths.append((chunk_path, index))
                        index += 1
                    payloads = self._transcribe_chunk_paths(
                        chunk_paths, request, duration_seconds, on_progress
                    )
                    texts = []
                    for payload in payloads:
                        text = self._payload_text(payload)
                        if text: texts.append(text); last_payload = payload
                return "\n\n".join(texts), last_payload
        except (OSError, EOFError, wave.Error) as exc:
            raise TranscriberError("无法分段读取 WAV：%s" % exc) from exc

    def _transcribe_chunk_paths(
        self,
        chunk_paths: list[tuple[Path, int]],
        request: TranscriptionRequest,
        duration_seconds: Optional[float],
        on_progress: Callable[[TranscriptionResult], None] | None = None,
    ) -> list[Dict[str, Any]]:
        if not chunk_paths:
            return []
        payloads: list[Dict[str, Any] | None] = [None] * len(chunk_paths)
        completed_prefix = 0

        def transcribe(item: tuple[Path, int]) -> Dict[str, Any]:
            return self._transcribe_chunk(item[0], request.language, item[1])

        payloads[0] = transcribe(chunk_paths[0])
        completed_prefix = 1
        if on_progress is not None:
            self._emit_progress(
                payloads[:completed_prefix],
                request,
                duration_seconds,
                completed_prefix,
                len(payloads),
                on_progress,
            )
        if len(chunk_paths) == 1:
            return [payload or {} for payload in payloads]

        with ThreadPoolExecutor(max_workers=min(self.chunk_workers, len(chunk_paths) - 1)) as executor:
            futures = {executor.submit(transcribe, item): item[1] for item in chunk_paths[1:]}
            for future in as_completed(futures):
                index = futures[future]
                payloads[index] = future.result()
                while completed_prefix < len(payloads) and payloads[completed_prefix] is not None:
                    completed_prefix += 1
                if on_progress is not None and completed_prefix:
                    self._emit_progress(
                        payloads[:completed_prefix],
                        request,
                        duration_seconds,
                        completed_prefix,
                        len(payloads),
                        on_progress,
                    )
        return [payload or {} for payload in payloads]

    def _emit_progress(
        self,
        payloads: list[Dict[str, Any] | None],
        request: TranscriptionRequest,
        duration_seconds: Optional[float],
        completed_chunks: int,
        total_chunks: int,
        on_progress: Callable[[TranscriptionResult], None],
    ) -> None:
        texts = [self._payload_text(payload or {}) for payload in payloads]
        text = "\n\n".join(item for item in texts if item).strip()
        if not text:
            return
        last_payload = next((payload for payload in reversed(payloads) if payload), {})
        on_progress(
            TranscriptionResult(
                text=text,
                language=str(last_payload.get("language") or request.language or ""),
                duration_seconds=duration_seconds,
                model_name=str(last_payload.get("model") or self.provider_name),
                metadata={
                    "service_url": self.base_url,
                    "chunked": "true",
                    "chunk_seconds": str(int(self.chunk_seconds)),
                    "partial": "true",
                    "completed_chunks": str(completed_chunks),
                    "total_chunks": str(total_chunks),
                },
            )
        )

    def _transcribe_chunk(self, audio_path: Path, language: Optional[str], index: int) -> Dict[str, Any]:
        for attempt in range(1, self.chunk_retries + 1):
            try:
                return self._transcribe_file(audio_path, language)
            except TranscriberError:
                if attempt >= self.chunk_retries:
                    raise
                print(
                    "QWEN_ASR_CHUNK_RETRY chunk=%d attempt=%d/%d"
                    % (index + 1, attempt + 1, self.chunk_retries),
                    flush=True,
                )
                time.sleep(min(8, 2 ** attempt))
        raise AssertionError("unreachable")

    @staticmethod
    def _wav_duration(path: Path) -> Optional[float]:
        try:
            with wave.open(str(path), "rb") as audio:
                rate = audio.getframerate()
                return audio.getnframes() / rate if rate else None
        except (OSError, EOFError, wave.Error):
            return None


def qwen_asr_available() -> bool:
    """Keep the phone from selecting Qwen while its LAN service is offline."""
    try:
        QwenAsrTranscriber(timeout_seconds=3).health()
        return True
    except TranscriberError:
        return False
