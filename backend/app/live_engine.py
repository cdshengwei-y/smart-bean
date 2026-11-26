"""录音豆直播流式引擎（2026-11-18）。

双引擎架构，移植自智能中枢 live_stream.py（cdshengwei-y/external-brain）：
- 录音豆固件录音中每 ~2s POST 一段 16k 单声道 s16le PCM 到接收器
  /live/stream；
- sherpa-onnx 流式 zipformer（14M int8，本地 CPU）增量解码，partial
  文字随说话实时增长——秒级粗字；
- 1.0~1.2s 尾部停顿（endpoint）= 一句说完：partial 定稿，该句音频交
  千问 ASR（153 机器）精转，完成后原地替换——粗字变准字；
- 聚合文本（精转句 + 未定稿 partial）直传 MinIO 笔记对象，Obsidian
  插件按 etag 增量拉取——出字粒度 = 插件轮询间隔；
- 权威链路（完整录音停止上传）接管后由 NOT EXISTS 规则自然遮蔽本行。

与 scanner 的 60s WAV 分段管线并存：流式会话活跃期间，scanner 跳过
该录音的分段转写（live_preview_state.json 里的 streaming 标记），停止
后完整录音照旧走权威链路。
"""

from __future__ import annotations

import math
import os
import queue
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, Optional

_MODEL_DIR = Path.home() / ".cache/sherpa-asr/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23"
_PCM_RATE = 16000
_MAX_UTTERANCE_SECONDS = 20.0
_SPEAK_RMS_DBFS = -45.0      # 近场说话判定（原始电平）
_MIN_UTTERANCE_RMS = -45.0   # 整句下限：只挡纯静音
_SESSION_TTL_SECONDS = 600.0  # 会话空闲过期

_lock = threading.RLock()
_recognizer = None
_finalize_pool = None
_sessions: Dict[str, dict] = {}  # key: f"{device_id}/{stem}"

# 异步解码（2026-11-26 提速）：/live/stream 的 POST 曾同步解码后才返回，
# 设备 HTTP 往返被解码耗时拉长，实测推流周期 2~5s、出字滞后 2~4s。
# 改为入队即返回，后台线程按到达顺序解码；RECORDER_LIVE_ASYNC=0 回退同步。
_DECODE_QUEUE_MAX_CHUNKS = 64  # ~4MB 上限，正常解码远快于实时不会堆积
_decode_queue: Optional["queue.Queue"] = None


def is_available() -> bool:
    directory = _MODEL_DIR
    return (directory / "tokens.txt").is_file() and (
        directory / "encoder-epoch-99-avg-1.int8.onnx"
    ).is_file()


def _get_recognizer():
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    import sherpa_onnx

    _recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(_MODEL_DIR / "tokens.txt"),
        encoder=str(_MODEL_DIR / "encoder-epoch-99-avg-1.int8.onnx"),
        decoder=str(_MODEL_DIR / "decoder-epoch-99-avg-1.int8.onnx"),
        joiner=str(_MODEL_DIR / "joiner-epoch-99-avg-1.int8.onnx"),
        num_threads=2,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.0,
        # endpoint 越早触发，重置窗口竞态越小（中枢实测教训）
        rule2_min_trailing_silence=1.0,
        rule3_min_utterance_length=20.0,
    )
    return _recognizer


def _get_finalize_pool():
    global _finalize_pool
    if _finalize_pool is None:
        from concurrent.futures import ThreadPoolExecutor

        _finalize_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="live-finalize"
        )
    return _finalize_pool


def _rms_dbfs(pcm_bytes: bytes) -> float:
    n = len(pcm_bytes) // 2
    if n == 0:
        return -99.0
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    rms = math.sqrt(sum(s * s for s in samples) / n)
    return 20 * math.log10(rms / 32768) if rms > 0 else -99.0


def _normalize_samples(samples):
    """峰值归一化到 -6dBFS（上限 ×20）：小声说话也能被流式引擎听见；
    纯底噪（峰值 <200）不放大，避免抬成幻觉语音。"""
    peak = max(abs(s) for s in samples) if samples else 0
    if 200 <= peak < 16000:
        gain = min(16000.0 / peak, 20.0)
        return [max(-32768, min(32767, int(s * gain))) for s in samples]
    return samples


def _new_session(key: str, device_id: str, stem: str) -> dict:
    recognizer = _get_recognizer()
    session = {
        "key": key,
        "device_id": device_id,
        "stem": stem,
        "stream": recognizer.create_stream(),
        "partial": "",
        "utterances": [],   # 定稿句（千问精转后原地替换）
        "utterance_pcm": bytearray(),
        "last_active": time.time(),
    }
    _sessions[key] = session
    return session


def _write_live_note(session: dict) -> None:
    """聚合文本（精转句 + 未定稿 partial）写入 DB 预览行并直传笔记。

    live 行状态 'live_streaming'：进插件同步列表（放宽列表包含它），
    但不被 note/AI 队列认领；权威行完成后被 NOT EXISTS 规则遮蔽。
    """
    from app.database import JobRepository
    from app.config import ScannerSettings

    ordered = list(session["utterances"])
    partial = str(session.get("partial") or "").strip()
    if partial:
        ordered.append(partial)
    joined = "\n".join(text for text in ordered if text).strip()
    if not joined:
        return
    device_id = session["device_id"]
    stem = session["stem"]
    try:
        settings = ScannerSettings.load()
        repository = JobRepository(settings.database_path)
        preview_object = "%s%s/live/%s/%s.wav" % (
            settings.recordings_prefix, device_id, stem, stem
        )
        note_object = "%s%s/%s.md" % (
            settings.notes_prefix, device_id, stem
        )
        job_id = repository.upsert_live_preview_job(
            preview_object, device_id, note_object
        )
        # 不走 save_transcript 状态机（避免触发 AI 队列）；live 行独立维护。
        repository.connection.execute(
            "UPDATE transcription_jobs SET status = 'live_streaming', "
            "transcript = ?, filtered_text = '', optimized_text = '', "
            "note_object_name = ?, language = ?, transcription_model = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (joined, note_object, settings.transcription_language,
             "live-stream", job_id),
        )
        repository.connection.commit()
        repository.close()
        store = _get_store()
        if store is not None:
            markdown = _pending_markdown(settings, joined, device_id, base_object=None,
                                          preview_object=preview_object)
            store.upload_note(note_object, markdown)
        print(
            "[%s] [LIVE] 笔记更新 %s 句数=%d" % (
                time.strftime("%H:%M:%S"), note_object, len(ordered)
            ),
            flush=True,
        )
    except Exception as error:  # noqa: BLE001 - 笔记链路异常不能杀死识别
        print(
            "[%s] [LIVE] 笔记更新失败: %s: %s" % (
                time.strftime("%H:%M:%S"), type(error).__name__, error
            ),
            flush=True,
        )


_store_singleton = None


def _get_store():
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    try:
        from app.config import ScannerSettings
        from app.storage.loader import load_recording_store

        _store_singleton = load_recording_store(ScannerSettings.load())
    except Exception as error:  # noqa: BLE001
        print("[LIVE] 存储加载失败: %s" % error, flush=True)
        return None
    return _store_singleton


def _pending_markdown(settings, text: str, device_id: str,
                      base_object, preview_object: str) -> str:
    """直播预览笔记的 Markdown（front-matter 与权威 RAW 笔记同构）。"""
    from app.transcription.contract import TranscriptionRequest, TranscriptionResult
    from app.service import TranscriptionService

    request = TranscriptionRequest(
        audio_path=Path(preview_object),
        source_object_name=preview_object,
        language=settings.transcription_language,
        metadata={"device_id": device_id},
    )
    result = TranscriptionResult(
        text=text,
        language=settings.transcription_language,
        duration_seconds=None,
        model_name="live-stream",
        metadata={"partial": "true", "live": "true"},
    )
    markdown = TranscriptionService.pending_markdown(
        request,
        result,
        {},
        ai_status="waiting",
        transcription_status="partial",
    )
    return markdown


def _refine_with_qwen(session: dict, index: int, pcm: bytes) -> None:
    """后台精转：句子 PCM 写临时 WAV 交千问，完成后原地替换并刷新笔记。"""
    import tempfile

    try:
        from app.config import ScannerSettings
        from app.transcription.contract import TranscriptionRequest
        from app.transcription.loader import load_transcriber

        settings = ScannerSettings.load()
        transcriber = load_transcriber(settings.transcriber_factory)
        with tempfile.TemporaryDirectory(prefix="recording-bean-live-") as directory:
            audio_path = Path(directory) / "utterance.wav"
            with wave.open(str(audio_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(_PCM_RATE)
                handle.writeframes(pcm)
            request = TranscriptionRequest(
                audio_path=audio_path,
                source_object_name="%s/%s" % (session["device_id"], session["stem"]),
                language=settings.transcription_language,
                metadata={"device_id": session["device_id"]},
            )
            result = transcriber.transcribe(request)
        refined = str(result.text or "").strip()
        with _lock:
            if index < len(session["utterances"]) and refined:
                session["utterances"][index] = refined
                print(
                    "[%s] [LIVE] 千问精转 #%d: %s" % (
                        time.strftime("%H:%M:%S"), index, refined
                    ),
                    flush=True,
                )
        _write_live_note(session)
    except Exception as error:  # noqa: BLE001 - 精转失败保留粗字
        print(
            "[%s] [LIVE] 千问精转失败: %s: %s" % (
                time.strftime("%H:%M:%S"), type(error).__name__, error
            ),
            flush=True,
        )


def _finalize_utterance(key: str, session: dict, recognizer) -> None:
    """停顿/超长定稿：partial 成句，音频交千问精转，尾部 1.2s 重喂救下句开头。"""
    partial = str(session.get("partial") or "").strip()
    pcm = bytes(session["utterance_pcm"])
    if recognizer.is_endpoint(session["stream"]):
        recognizer.reset(session["stream"])
    session["partial"] = ""
    session["utterance_pcm"] = bytearray()

    rms_db = _rms_dbfs(pcm)
    if rms_db < _MIN_UTTERANCE_RMS:
        print(
            "[%s] [LIVE] 远场噪声句丢弃 rms=%.1f partial=%r" % (
                time.strftime("%H:%M:%S"), rms_db, partial
            ),
            flush=True,
        )
    elif partial:
        index = len(session["utterances"])
        session["utterances"].append(partial)
        print(
            "[%s] [LIVE] 定稿 #%d rms=%.1f text=%r" % (
                time.strftime("%H:%M:%S"), index, rms_db, partial
            ),
            flush=True,
        )
        if len(pcm) >= _PCM_RATE * 2 * 0.4:
            _get_finalize_pool().submit(
                _refine_with_qwen, session, index, pcm
            )

    # 尾部重喂：救回卡在重置窗口里的下一句开头（中枢实测教训）
    tail = pcm[-int(_PCM_RATE * 1.2) * 2:]
    if tail:
        n = len(tail) // 2
        tail_samples = _normalize_samples(list(struct.unpack(f"<{n}h", tail)))
        stream = session["stream"]
        stream.accept_waveform(_PCM_RATE, [s / 32768.0 for s in tail_samples])
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        session["partial"] = str(recognizer.get_result(stream) or "").strip()
        session["utterance_pcm"] = bytearray(tail)

    _write_live_note(session)


def _async_decode_enabled() -> bool:
    return os.environ.get("RECORDER_LIVE_ASYNC", "1") != "0"


def _get_decode_queue() -> "queue.Queue":
    global _decode_queue
    if _decode_queue is None:
        _decode_queue = queue.Queue(maxsize=_DECODE_QUEUE_MAX_CHUNKS)
        worker = threading.Thread(
            target=_decode_worker, name="live-decode", daemon=True
        )
        worker.start()
    return _decode_queue


def _decode_worker() -> None:
    """按到达顺序解码设备推来的 PCM；异常不吞任务，保证会话状态机连续。"""
    while True:
        item = _decode_queue.get()
        if item is None:
            continue
        device_id, stem, pcm_bytes, received_at = item
        queue_wait_ms = (time.time() - received_at) * 1000.0
        try:
            decode_start = time.time()
            # 会话状态（partial/utterances/stream）必须串行改写；
            # _lock 是 RLock，worker 线程内再进 snapshot() 可重入。
            with _lock:
                _feed_pcm_locked(device_id, stem, pcm_bytes)
            decode_ms = (time.time() - decode_start) * 1000.0
            # 观测指标：queue_wait 是设备→解码的滞后面，decode 是引擎开销
            print(
                "[%s] [LIVE][DBG] decode queue_wait=%.0fms decode=%.0fms" % (
                    time.strftime("%H:%M:%S"), queue_wait_ms, decode_ms
                ),
                flush=True,
            )
        except Exception as error:  # noqa: BLE001
            print(
                "[%s] [LIVE] 异步解码失败 %s/%s: %s: %s" % (
                    time.strftime("%H:%M:%S"), device_id, stem,
                    type(error).__name__, error,
                ),
                flush=True,
            )


def feed_pcm(device_id: str, stem: str, pcm_bytes: bytes) -> Dict[str, Any]:
    """设备推来的 PCM → 流式解码 → 更新 partial / 定稿。

    异步模式：PCM 入队立即返回当前快照（设备 HTTP 往返 ≤ 数十 ms），
    解码由后台线程串行执行；会话创建/保活也在入队前同步完成，保证
    会话开始日志与 feed# 序号语义不变。
    """
    key = "%s/%s" % (device_id, stem)
    with _lock:
        session = _sessions.get(key)
        if session is None:
            _get_recognizer()
            _new_session(key, device_id, stem)
            session = _sessions[key]
            print(
                "[%s] [LIVE] 会话开始 %s" % (time.strftime("%H:%M:%S"), key),
                flush=True,
            )
        session["last_active"] = time.time()

    if _async_decode_enabled() and len(pcm_bytes) > 0:
        try:
            _get_decode_queue().put_nowait(
                (device_id, stem, pcm_bytes, time.time())
            )
            return snapshot(device_id, stem)
        except queue.Full:
            # 队列满 = 解码持续落后于实时：丢弃本包防雪崩，打印告警
            print(
                "[%s] [LIVE] 解码队列已满，丢弃 %d 字节" % (
                    time.strftime("%H:%M:%S"), len(pcm_bytes)
                ),
                flush=True,
            )
            return snapshot(device_id, stem)

    with _lock:
        return _feed_pcm_locked(device_id, stem, pcm_bytes)


def _feed_pcm_locked(device_id: str, stem: str, pcm_bytes: bytes) -> Dict[str, Any]:
    """同步解码路径（调用方必须已持有 _lock）。"""
    recognizer = _get_recognizer()
    key = "%s/%s" % (device_id, stem)
    session = _sessions.get(key)
    if session is None:
        # 兜底：异步路径已在 feed_pcm 创建会话，正常不会走到这里；
        # 同步模式（RECORDER_LIVE_ASYNC=0）靠这里建会话。
        session = _new_session(key, device_id, stem)
        print(
            "[%s] [LIVE] 会话开始 %s" % (time.strftime("%H:%M:%S"), key),
            flush=True,
        )
    session["last_active"] = time.time()

    n = len(pcm_bytes) // 2
    if n == 0:
        return snapshot(device_id, stem)
    raw = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    raw_rms = math.sqrt(sum(s * s for s in raw) / n)
    raw_rms_db = 20 * math.log10(raw_rms / 32768) if raw_rms > 0 else -99.0
    samples = _normalize_samples(list(raw))
    stream = session["stream"]
    # 必须归一到 [-1, 1] 浮点：整型直喂振幅超万倍，识别直接乱码
    stream.accept_waveform(_PCM_RATE, [s / 32768.0 for s in samples])
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    partial = str(recognizer.get_result(stream) or "").strip()
    if partial != session.get("_prev_partial"):
        session["_prev_partial"] = partial
        print(
            "[%s] [LIVE] decode rms=%.1f partial=%r" % (
                time.strftime("%H:%M:%S"), raw_rms_db, partial
            ),
            flush=True,
        )
    session["partial"] = partial

    session["utterance_pcm"].extend(pcm_bytes)
    max_pcm = int(_MAX_UTTERANCE_SECONDS * _PCM_RATE) * 2
    if recognizer.is_endpoint(stream) or len(session["utterance_pcm"]) >= max_pcm:
        try:
            _finalize_utterance(key, session, recognizer)
        except Exception as error:  # noqa: BLE001 - 定稿崩了不能静默死亡
            print(
                "[%s] [LIVE] finalize 异常: %s: %s" % (
                    time.strftime("%H:%M:%S"), type(error).__name__, error
                ),
                flush=True,
            )
            session["utterance_pcm"].extend(pcm_bytes)
    return snapshot(device_id, stem)


def snapshot(device_id: str, stem: str) -> Dict[str, Any]:
    """当前会话快照（APP 实时字幕展示用）。"""
    key = "%s/%s" % (device_id, stem)
    with _lock:
        session = _sessions.get(key)
        if session is None:
            return {"active": False, "sentences": [], "partial": ""}
        ordered = list(session["utterances"])
        partial = str(session.get("partial") or "").strip()
        if partial:
            ordered.append(partial)
        return {
            "active": True,
            "partial": partial,
            "sentences": ordered,
            "text": "\n".join(text for text in ordered if text),
            "updated_at": time.time(),
        }


def sweep_expired() -> None:
    """空闲会话过期清理（避免长驻内存）。"""
    with _lock:
        now = time.time()
        for key in [
            key for key, session in _sessions.items()
            if now - session["last_active"] > _SESSION_TTL_SECONDS
        ]:
            print("[%s] [LIVE] 会话过期 %s" % (time.strftime("%H:%M:%S"), key),
                  flush=True)
            _sessions.pop(key, None)
