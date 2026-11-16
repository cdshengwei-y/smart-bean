"""Discover MinIO recordings and turn each one into a Markdown note."""

from __future__ import annotations

import json
import os
import struct
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict

from .config import ScannerSettings
from .database import JobRepository, StoredTextJob, TranscriptionJob
from .service import TranscriptionService
from .transcription.contract import TranscriptionRequest, TranscriptionResult


def validate_audio_capture(audio_path: Path) -> None:
    """Reject the non-zero constant PCM produced by a dead PDM data path."""
    with wave.open(str(audio_path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            return

        first_frame = audio.readframes(1)
        if len(first_frame) != 2:
            return
        first_sample = struct.unpack("<h", first_frame)[0]
        if first_sample == 0:
            return

        while True:
            chunk = audio.readframes(32768)
            if not chunk:
                break
            for offset in range(0, len(chunk) - 1, 2):
                if chunk[offset : offset + 2] != first_frame:
                    return

    raise ValueError(
        "MICROPHONE_NO_SIGNAL: WAV PCM 全程为同一个非零采样值，麦克风数据链路无有效变化。"
    )


class RecordingScanner:
    def __init__(
        self,
        settings: ScannerSettings,
        store: Any,
        repository: JobRepository,
        transcription_service: TranscriptionService,
        obsidian_sync: Any = None,
        profile_store: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.repository = repository
        self.transcription_service = transcription_service
        self.obsidian_sync = obsidian_sync
        self.profile_store = profile_store
        self.progress_note_interval_seconds = max(
            10.0,
            float(os.getenv("TRANSCRIPTION_PROGRESS_NOTE_INTERVAL_SECONDS", "30")),
        )

    def asr_available(self) -> bool:
        health = getattr(self.transcription_service.transcriber, "health", None)
        if not callable(health):
            return True
        try:
            health()
            return True
        except Exception as exc:
            print("ASR_UNAVAILABLE: %s" % exc, flush=True)
            return False

    def discover(self) -> tuple[int, int]:
        recordings = self.store.list_recordings()
        discovered = self.repository.discover(recordings)
        missing = self.repository.mark_missing(recordings)
        return discovered, missing

    def process_job(self, job: TranscriptionJob) -> bool:
        """Download and transcribe once; AI processing is handled separately."""
        run_id: int | None = None
        download_seconds = 0.0
        asr_seconds = 0.0
        try:
            model_id = self._current_model_id()
            run_id = self.repository.start_transcription_run(job.id, model_id)
            with tempfile.TemporaryDirectory(prefix="recording-bean-") as directory:
                filename = Path(job.object_name).name
                audio_path = Path(directory) / filename
                download_started = time.monotonic()
                self.download_recording(job.object_name, audio_path)
                download_seconds = time.monotonic() - download_started
                validate_audio_capture(audio_path)
                request = TranscriptionRequest(
                    audio_path=audio_path,
                    source_object_name=job.object_name,
                    language=self.settings.transcription_language,
                    metadata={"device_id": job.device_id},
                )
                note_object_name = self.store.note_object_name(job.object_name)
                progress_writer = self._progress_note_writer(job, request, note_object_name)
                asr_started = time.monotonic()
                result = self._service_for_model(model_id).transcribe_with_progress(
                    request,
                    on_progress=progress_writer,
                )
                asr_seconds = time.monotonic() - asr_started
                self.repository.save_transcript(
                    job.id,
                    result.text,
                    note_object_name,
                    result.language,
                    result.model_name,
                    result.duration_seconds,
                    download_seconds,
                    asr_seconds,
                    transcription_model_id=model_id,
                )
                self.repository.complete_transcription_run(
                    run_id,
                    result.model_name,
                    result.language,
                    result.duration_seconds,
                    download_seconds,
                    asr_seconds,
                    result.text,
                )
            print(
                "TRANSCRIBED %s download=%.1fs asr=%.1fs"
                % (job.object_name, download_seconds, asr_seconds),
                flush=True,
            )
            return True
        except Exception as exc:
            if run_id is not None:
                self.repository.fail_transcription_run(
                    run_id,
                    str(exc),
                    download_seconds,
                    asr_seconds,
                )
            self.repository.fail_transcription(job.id, str(exc))
            print("TRANSCRIPTION_FAIL %s: %s" % (job.object_name, exc), flush=True)
            return False

    def _current_model_id(self) -> str:
        transcriber = self.transcription_service.transcriber
        selected_model_id = getattr(transcriber, "selected_model_id", "")
        if selected_model_id:
            return str(selected_model_id)
        return str(getattr(transcriber, "provider_name", "unknown-asr"))

    def _service_for_model(self, model_id: str) -> TranscriptionService:
        builder = getattr(self.transcription_service.transcriber, "build_model", None)
        if callable(builder):
            return TranscriptionService(builder(model_id))
        return self.transcription_service

    def process_replay(self, run: Any) -> bool:
        """Re-run one historical ASR model without changing the current note."""
        builder = getattr(self.transcription_service.transcriber, "build_model", None)
        if not callable(builder):
            self.repository.fail_transcription_run(
                run.id,
                "当前转写适配器不支持按历史模型回放。",
            )
            return False
        download_seconds = 0.0
        asr_seconds = 0.0
        try:
            service = self._service_for_model(run.model_id)
            with tempfile.TemporaryDirectory(prefix="recording-bean-replay-") as directory:
                audio_path = Path(directory) / Path(run.object_name).name
                download_started = time.monotonic()
                self.download_recording(run.object_name, audio_path)
                download_seconds = time.monotonic() - download_started
                request = TranscriptionRequest(
                    audio_path=audio_path,
                    source_object_name=run.object_name,
                    language=self.settings.transcription_language,
                    metadata={"device_id": run.device_id, "replay_run_id": str(run.id)},
                )
                asr_started = time.monotonic()
                result = service.transcribe_with_progress(request)
                asr_seconds = time.monotonic() - asr_started
            self.repository.complete_transcription_run(
                run.id,
                result.model_name,
                result.language,
                result.duration_seconds,
                download_seconds,
                asr_seconds,
                result.text,
            )
            print(
                "TRANSCRIPTION_REPLAY_DONE %s model=%s asr=%.1fs"
                % (run.object_name, run.model_id, asr_seconds),
                flush=True,
            )
            return True
        except Exception as exc:
            self.repository.fail_transcription_run(
                run.id,
                str(exc),
                download_seconds,
                asr_seconds,
            )
            print("TRANSCRIPTION_REPLAY_FAIL %s: %s" % (run.object_name, exc), flush=True)
            return False

    def _progress_note_writer(
        self,
        job: TranscriptionJob,
        request: TranscriptionRequest,
        note_object_name: str,
    ):
        profile = self.profile_store.get(job.device_id) if self.profile_store else {}
        last_write_at = 0.0
        last_text = ""

        def write(result: TranscriptionResult) -> None:
            nonlocal last_write_at, last_text
            text = result.text.strip()
            if not text or text == last_text:
                return
            now = time.monotonic()
            is_final_chunk = (
                result.metadata.get("completed_chunks")
                and result.metadata.get("completed_chunks") == result.metadata.get("total_chunks")
            )
            if (
                not is_final_chunk
                and last_write_at
                and now - last_write_at < self.progress_note_interval_seconds
            ):
                return
            markdown = self.transcription_service.pending_markdown(
                request,
                result,
                profile,
                ai_status="waiting",
                transcription_status="partial",
            )
            started = time.monotonic()
            try:
                self.store.upload_note(note_object_name, markdown)
                synced = self.obsidian_sync.sync_once() if self.obsidian_sync is not None else 0
            except Exception as exc:
                print("PARTIAL_NOTE_FAIL %s: %s" % (job.object_name, exc), flush=True)
                return
            last_write_at = now
            last_text = text
            print(
                "PARTIAL_NOTE %s chunks=%s/%s upload=%.1fs synced=%d"
                % (
                    job.object_name,
                    result.metadata.get("completed_chunks", "?"),
                    result.metadata.get("total_chunks", "?"),
                    time.monotonic() - started,
                    synced,
                ),
                flush=True,
            )

        return write

    def _live_state_path(self) -> Path:
        return self.settings.database_path.parent / "live_preview_state.json"

    def _load_live_state(self) -> Dict[str, Any]:
        path = self._live_state_path()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_live_state(self, state: Dict[str, Any]) -> None:
        path = self._live_state_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(path)

    def _parse_live_base(self, object_name: str) -> tuple[str, str] | None:
        """recordings/<设备>/live/<录音名>/<分段>.wav -> (权威对象名, 设备 ID)。"""
        rel = object_name.removeprefix(self.settings.recordings_prefix)
        parts = Path(rel).parts
        if len(parts) != 4 or parts[1] != "live":
            return None
        device_id = parts[0]
        base_object = self.settings.recordings_prefix + device_id + "/" + parts[2] + ".wav"
        return base_object, device_id

    def _transcribe_live_segment(self, object_name: str) -> str:
        with tempfile.TemporaryDirectory(prefix="recording-bean-live-") as directory:
            audio_path = Path(directory) / Path(object_name).name
            self.store.download_recording(object_name, audio_path)
            validate_audio_capture(audio_path)
            parsed = self._parse_live_base(object_name)
            device_id = parsed[1] if parsed else ""
            request = TranscriptionRequest(
                audio_path=audio_path,
                source_object_name=object_name,
                language=self.settings.transcription_language,
                metadata={"device_id": device_id},
            )
            result = self.transcription_service.transcribe(request)
            return result.text

    def _upload_live_preview(self, base_object: str, entry: Dict[str, Any]) -> None:
        parsed = self._parse_live_base(
            base_object.replace(self.settings.recordings_prefix, "", 1)
        )
        device_id = entry.get("device_id") or (parsed[1] if parsed else "")
        ordered = sorted(entry.get("segments", {}).items())
        texts = [str(value.get("text") or "") for _, value in ordered]
        joined = "\n\n".join(text for text in texts if text).strip()
        if not joined:
            return
        result = TranscriptionResult(
            text=joined,
            language=self.settings.transcription_language,
            duration_seconds=None,
            model_name="live-preview",
            metadata={"partial": "true", "live": "true", "segments": str(len(texts))},
        )
        profile = self.profile_store.get(device_id) if self.profile_store else {}
        request = TranscriptionRequest(
            audio_path=Path(base_object),
            source_object_name=base_object,
            language=self.settings.transcription_language,
            metadata={"device_id": device_id},
        )
        markdown = self.transcription_service.pending_markdown(
            request,
            result,
            profile,
            ai_status="waiting",
            transcription_status="partial",
        )
        note_object = self.store.note_object_name(base_object)
        self.store.upload_note(note_object, markdown)
        synced = self.obsidian_sync.sync_once() if self.obsidian_sync is not None else 0
        print(
            "LIVE_PREVIEW %s segments=%d synced=%d"
            % (base_object, len(texts), synced),
            flush=True,
        )

    def process_live_previews(self) -> int:
        """直播预览：逐段转写分段副本，聚合后流式刷新同一篇笔记。

        完整录音一旦被权威链路建任务接管，预览即停止并清理分段对象。
        """
        try:
            segments = self.store.list_live_segments()
        except Exception as exc:
            print("LIVE_LIST_FAIL %s" % exc, flush=True)
            return 0
        bases: Dict[str, List[Any]] = {}
        for item in segments:
            parsed = self._parse_live_base(item.object_name)
            if parsed is None:
                continue
            base_object, device_id = parsed
            bases.setdefault(base_object, []).append(item)
        state = self._load_live_state()
        processed = 0
        for base_object, items in bases.items():
            entry = state.get(base_object)
            if entry is None:
                parsed = self._parse_live_base(items[0].object_name)
                device_id = parsed[1] if parsed else ""
                entry = {"device_id": device_id, "segments": {}}
            if self.repository.has_job_for_object(base_object):
                # 权威链路（完整录音）已接管：清空预览状态并删除分段对象。
                if entry.get("segments"):
                    for item in items:
                        try:
                            self.store.delete_recording(item.object_name)
                        except Exception as exc:
                            print("LIVE_CLEANUP_FAIL %s: %s" % (item.object_name, exc), flush=True)
                if state.get(base_object) is not None:
                    state.pop(base_object, None)
                    self._save_live_state(state)
                continue
            changed = False
            for item in sorted(items, key=lambda value: value.object_name):
                segment_name = Path(item.object_name).name
                known = entry["segments"].get(segment_name)
                if known and known.get("etag") == item.etag:
                    continue
                try:
                    text = self._transcribe_live_segment(item.object_name)
                except Exception as exc:
                    print(
                        "LIVE_SEG_FAIL %s: %s" % (item.object_name, exc), flush=True
                    )
                    continue
                entry["segments"][segment_name] = {"etag": item.etag, "text": text}
                changed = True
            if changed:
                state[base_object] = entry
                self._save_live_state(state)
                self._upload_live_preview(base_object, entry)
                processed += 1
        active = set(bases.keys())
        stale_keys = [key for key in state.keys() if key not in active]
        if stale_keys:
            for key in stale_keys:
                state.pop(key, None)
            self._save_live_state(state)
        return processed

    def process_note_job(self, job: StoredTextJob) -> bool:
        try:
            profile = self.profile_store.get(job.device_id) if self.profile_store else {}
            request = TranscriptionRequest(
                audio_path=Path(Path(job.object_name).name),
                source_object_name=job.object_name,
                language=job.language or self.settings.transcription_language,
                metadata={"device_id": job.device_id},
            )
            result = TranscriptionResult(
                text=job.transcript,
                language=job.language,
                duration_seconds=job.duration_seconds,
                model_name=job.transcription_model,
                metadata={"empty_transcript": "true"} if not job.transcript.strip() else {},
            )
            markdown = self.transcription_service.pending_markdown(request, result, profile)
            started = time.monotonic()
            self.store.upload_note(job.note_object_name, markdown)
            synced = self.obsidian_sync.sync_once() if self.obsidian_sync is not None else 0
            note_seconds = time.monotonic() - started
            self.repository.mark_note_ready(job.id, note_seconds)
            print(
                "RAW_NOTE %s -> %s synced=%d"
                % (job.object_name, job.note_object_name, synced),
                flush=True,
            )
            return True
        except Exception as exc:
            self.repository.fail_note(job.id, str(exc))
            print("RAW_NOTE_FAIL %s: %s" % (job.object_name, exc), flush=True)
            return False

    def download_recording(self, object_name: str, audio_path: Path) -> None:
        retries = max(1, int(os.getenv("RECORDING_DOWNLOAD_RETRIES", "3")))
        for attempt in range(1, retries + 1):
            try:
                self.store.download_recording(object_name, audio_path)
                return
            except Exception:
                audio_path.unlink(missing_ok=True)
                if attempt >= retries:
                    raise
                print(
                    "RECORDING_DOWNLOAD_RETRY %s attempt=%d/%d"
                    % (object_name, attempt + 1, retries),
                    flush=True,
                )
                time.sleep(min(8, 2 ** attempt))

    def run_once(self, scan_only: bool = False, report_idle: bool = True) -> Dict[str, int]:
        discovered, missing = self.discover()
        processed = 0
        failed = 0
        replayed = 0
        replay_failed = 0
        if not scan_only:
            while True:
                replay = self.repository.claim_next_transcription_replay()
                if replay is None:
                    break
                if self.process_replay(replay):
                    replayed += 1
                else:
                    replay_failed += 1
            asr_ready = self.asr_available()
            while True:
                did_work = False
                while True:
                    note_job = self.repository.claim_next_note(
                        self.settings.max_attempts,
                        self.settings.retry_delay_seconds,
                    )
                    if note_job is None:
                        break
                    did_work = True
                    if self.process_note_job(note_job):
                        processed += 1
                    else:
                        failed += 1
                if not did_work and asr_ready:
                    # 主队列空闲时处理直播预览分段，避免抢占正式转写的算力。
                    self.process_live_previews()
                if not asr_ready:
                    break
                job = self.repository.claim_next_transcription(
                    self.settings.max_attempts,
                    self.settings.retry_delay_seconds,
                )
                if job is None:
                    break
                did_work = True
                if self.process_job(job):
                    processed += 1
                else:
                    failed += 1
                if not did_work:
                    break
        counts = self.repository.status_counts()
        synced = self.obsidian_sync.sync_once() if self.obsidian_sync is not None else 0
        if report_idle or any((discovered, missing, processed, failed, synced)):
            print(
                "SCAN discovered=%d missing=%d processed=%d failed=%d synced=%d status=%s"
                % (discovered, missing, processed, failed, synced, counts),
                flush=True,
            )
        return {
            "discovered": discovered,
            "missing": missing,
            "processed": processed,
            "failed": failed,
            "replayed": replayed,
            "replay_failed": replay_failed,
            "synced": synced,
            **counts,
        }

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once(report_idle=False)
            except Exception as exc:
                print("SCAN ERROR: %s" % exc, flush=True)
            time.sleep(self.settings.scan_interval_seconds)
