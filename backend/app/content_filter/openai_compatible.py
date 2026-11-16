"""Work-content filter backed by an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Mapping
import re
import hashlib
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .contract import ContentFilterError, WorkContentFilterResult
from .profile import DEFAULT_TYPES
from ..text_cleanup import remove_discourse_fillers


FILTER_PROMPT = """你是录音转写内容筛选器。你的任务是从输入文字中选择需要保留的原文片段。
这是严格的删除式筛选，不是总结、改写或润色：模型不得生成输入中不存在的任何字词，不得修正错词，不得合并或重排句子。
返回的每个 keep_segments 元素必须是输入文字中逐字连续、原样存在的完整片段（包括原有标点和空格）；后端会逐字核验，不符合的片段会被丢弃。
尽量按完整句子或完整段落返回，保留理解工作事项所需的上下文；片段之间保持输入顺序。
只保留与用户选择的工作类型直接相关、并且可执行或可追踪的工作信息，例如任务、需求、bug、技术方案、决策、风险、待办、负责人、时间节点、项目事实或复盘结论。
不要保留泛泛的测试话术、操作说明或系统自测内容。默认删除关于录音、转写、筛选、文字优化、Obsidian 显示、最终笔记覆盖、处理链路耗时、速度是否足够快、筛选是否生效的元说明；除非用户明确把它记录为项目需求、缺陷、方案或待办。
删除私人生活、闲聊、娱乐、家庭、情绪宣泄及其他无关内容。
如果没有符合条件的内容，返回空数组。只返回 JSON：{\"keep_segments\":[\"输入中的原文片段\"]}。"""

MEETING_FILTER_PROMPT = """你是会议录音内容筛选器。你的任务是找出输入文字中应删除的非会议内容。
这是严格的删除式筛选，不是总结、改写或润色：模型不得生成输入中不存在的任何字词，不得修正错词，不得合并或重排句子。
返回的每个 drop_segments 元素必须是输入文字中逐字连续、原样存在的完整片段（包括原有标点和空格）；后端会逐字核验，不符合的片段不会被删除。
只删除明确与会议无关的私人生活、寒暄闲聊、娱乐、家庭或情绪宣泄。必须保留会议中的议题、汇报、技术讨论、项目进展、决策、分歧、问题、风险、待办、负责人、截止时间、上下文和下一步计划。
无法确定是否属于会议时必须保留。没有应删除内容时返回空数组。只返回 JSON：{\"drop_segments\":[\"输入中的原文片段\"]}。"""

OPTIMIZE_PROMPT = """不要展示或进行推理，立即按要求输出 JSON。
你是录音转写文字校对器。只返回当前文字块中必须执行的保守修改清单，不要重新输出全文。
允许修改：ASR异常连续重复、口吃、明确的口头填充词（例如独立的“嗯”“呃”）、明显错误的标点/断句/空格，以及上下文高度确定的同音字或专业术语。
禁止筛选话题、概括、润色改写、改变原意或添加事实；不确定时不要修改。
每项 from 必须是当前文字块中逐字存在且只出现一次的连续原文；若短词出现多次，请扩大 from 的上下文使其唯一。to 是替换后的完整片段，可为空字符串。
不同修改项不得重叠。没有可靠修改时返回空数组。
只返回 JSON：{\"edits\":[{\"from\":\"原文精确片段\",\"to\":\"替换后片段\"}]}。"""


class _RetryableContentFilterError(ContentFilterError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float = 0,
        increase_tokens: bool = False,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        self.increase_tokens = increase_tokens


class OpenAICompatibleWorkContentFilter:
    def __init__(self) -> None:
        base_url = os.getenv("WORK_FILTER_BASE_URL", "").strip().rstrip("/")
        model = os.getenv("WORK_FILTER_MODEL", "").strip()
        if not base_url or not model:
            raise ContentFilterError(
                "未配置工作内容筛选模型：请设置 WORK_FILTER_BASE_URL 和 WORK_FILTER_MODEL。"
            )
        path = os.getenv("WORK_FILTER_CHAT_PATH", "/v1/chat/completions").strip()
        self.url = base_url + "/" + path.lstrip("/")
        self.model = model
        self.api_key = os.getenv("WORK_FILTER_API_KEY", "").strip()
        self.timeout_seconds = max(5.0, float(os.getenv("WORK_FILTER_TIMEOUT_SECONDS", "180")))
        self.workers = max(1, min(4, int(os.getenv("WORK_FILTER_WORKERS", "1"))))
        self.retries = max(1, int(os.getenv("WORK_FILTER_RETRIES", "3")))
        self.max_tokens = max(256, int(os.getenv("WORK_FILTER_MAX_TOKENS", "2048")))
        self.max_retry_tokens = max(
            self.max_tokens,
            int(os.getenv("WORK_FILTER_MAX_RETRY_TOKENS", "4096")),
        )
        self.retry_delay_seconds = max(
            0.0, float(os.getenv("WORK_FILTER_RETRY_DELAY_SECONDS", "2"))
        )
        self.rate_limit_delay_seconds = max(
            1.0, float(os.getenv("WORK_FILTER_RATE_LIMIT_DELAY_SECONDS", "5"))
        )
        default_cache = Path(__file__).resolve().parents[2] / "data" / "content_filter_cache"
        self.cache_dir = Path(os.getenv("WORK_FILTER_CACHE_DIR", str(default_cache))).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._inflight_lock = threading.Lock()
        self._inflight: dict[str, Future[dict[str, Any]]] = {}

    @property
    def provider_name(self) -> str:
        return self.model

    def optimize(self, transcript: str) -> str:
        if not transcript.strip():
            return ""
        chunks = self._chunks(transcript)
        def process(item: tuple[int, str]) -> str:
            index, chunk = item
            result = self._cached_completion(
                "optimize", OPTIMIZE_PROMPT, chunk,
                "recording_text_edits_%d" % index,
                {
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                            },
                            "required": ["from", "to"],
                            "additionalProperties": False,
                        },
                    }
                },
                ["edits"],
            )
            edits = result.get("edits")
            if not isinstance(edits, list):
                raise ContentFilterError("文字优化服务没有返回 edits 数组。")
            return remove_discourse_fillers(self._apply_edits(chunk, edits))
        if self.workers == 1:
            outputs = [process(item) for item in enumerate(chunks)]
        else:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(chunks))) as executor:
                outputs = list(executor.map(process, enumerate(chunks)))
        return "\n\n".join(item for item in outputs if item)

    def filter(self, transcript: str, profile: Mapping[str, object] | None = None) -> WorkContentFilterResult:
        if not transcript.strip():
            return WorkContentFilterResult(text="", model_name=self.provider_name)
        profile = profile or {}
        # Text optimization is a mandatory normalization stage, not a user option.
        enabled = bool(profile.get("enabled", False))
        scene = str(profile.get("recording_scene", "work"))
        if scene == "all":
            return WorkContentFilterResult(
                text=transcript.strip(),
                model_name=self.provider_name,
                metadata={"optimized_text": transcript.strip(), "filter_enabled": "false", "optimization_enabled": "true", "recording_scene": "all"},
            )
        type_names = [DEFAULT_TYPES.get(str(item), str(item)) for item in (profile.get("work_types") or [])]
        if not enabled:
            return WorkContentFilterResult(text=transcript.strip(), model_name=self.provider_name, metadata={"optimized_text": transcript.strip(), "filter_enabled": "false", "optimization_enabled": "true", "recording_scene": scene})
        if scene == "meeting":
            instruction = MEETING_FILTER_PROMPT
            instruction += "\n工作类型只用于提示重点（会议重点），不能据此删除其他会议议题。"
            result_key = "drop_segments"
        else:
            instruction = FILTER_PROMPT
            instruction += "\n记录场景：普通工作内容。只有与所选工作类型或用户补充直接相关的内容才保留；不要因为泛泛提到工作就保留无关领域内容。"
            result_key = "keep_segments"
        instruction += "\n工作筛选：开启。工作类型：%s。用户补充：%s。排除内容：%s。判断原则：保守。" % (
            "、".join(type_names),
            profile.get("custom_description", ""), profile.get("excluded_content", ""))
        filter_chunk_chars = max(300, min(1600, int(os.getenv("WORK_FILTER_FILTER_CHUNK_CHARS", "600"))))
        chunks = self._chunks(transcript, maximum=filter_chunk_chars)
        def process(item: tuple[int, str]) -> str:
            index, chunk = item
            result_payload = self._cached_completion(
                "filter", instruction, chunk,
                "recording_work_filter_%d" % index,
                {
                    result_key: {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                }, [result_key])
            segments = result_payload.get(result_key, [])
            if not isinstance(segments, list):
                raise ContentFilterError("工作内容筛选服务没有返回 %s 数组。" % result_key)
            if scene == "meeting":
                return self._drop_exact_segments(chunk, segments)
            kept = self._keep_exact_segments(chunk, segments)
            kept = self._drop_obvious_normal_work_noise(kept, profile)
            if kept.strip():
                return kept
            return self._fallback_keep_work_sentences(chunk, profile)
        if self.workers == 1:
            outputs = [process(item) for item in enumerate(chunks)]
        else:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(chunks))) as executor:
                outputs = list(executor.map(process, enumerate(chunks)))
        filter_mode = "exact_deletions" if scene == "meeting" else "exact_segments"
        return WorkContentFilterResult(text="\n\n".join(outputs), model_name=self.provider_name, metadata={"optimized_text": transcript.strip(), "filter_enabled": "true", "optimization_enabled": "true", "filter_mode": filter_mode, "recording_scene": scene})

    def _cached_completion(
        self,
        stage: str,
        instruction: str,
        transcript: str,
        schema_name: str,
        properties: dict[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        digest = hashlib.sha256(
            (self.model + "\n" + stage + "\n" + instruction + "\n" + transcript).encode("utf-8")
        ).hexdigest()
        path = self.cache_dir / (digest + ".json")
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, dict): return cached
        except (OSError, ValueError, TypeError):
            pass
        with self._inflight_lock:
            future = self._inflight.get(digest)
            if future is None:
                future = Future()
                self._inflight[digest] = future
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            return future.result()

        try:
            last_error: Exception | None = None
            max_tokens = self.max_tokens
            for attempt in range(1, self.retries + 1):
                try:
                    result = self._completion(
                        instruction,
                        transcript,
                        schema_name,
                        properties,
                        required,
                        max_tokens=max_tokens,
                    )
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(self.cache_dir), delete=False) as temporary:
                        json.dump(result, temporary, ensure_ascii=False)
                        temporary_path = Path(temporary.name)
                    temporary_path.replace(path)
                    future.set_result(result)
                    return result
                except _RetryableContentFilterError as exc:
                    last_error = exc
                    if exc.increase_tokens:
                        max_tokens = min(max_tokens * 2, self.max_retry_tokens)
                    if attempt >= self.retries:
                        break
                    delay = max(
                        exc.retry_after_seconds,
                        self.retry_delay_seconds * attempt,
                    )
                    if delay:
                        time.sleep(delay)
                except ContentFilterError as exc:
                    last_error = exc
                    break
            error = last_error or ContentFilterError("文本处理失败。")
            future.set_exception(error)
            raise error
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            with self._inflight_lock:
                if self._inflight.get(digest) is future:
                    del self._inflight[digest]

    @staticmethod
    def _chunks(text: str, maximum: int = 600) -> list[str]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        chunks, current = [], ""
        for paragraph in paragraphs:
            parts = [paragraph[i:i + maximum] for i in range(0, len(paragraph), maximum)]
            for part in parts:
                candidate = (current + "\n\n" + part).strip() if current else part
                if current and len(candidate) > maximum:
                    chunks.append(current); current = part
                else:
                    current = candidate
        if current: chunks.append(current)
        return chunks

    @staticmethod
    def _apply_edits(text: str, edits: list[Any]) -> str:
        """Safely apply unique, non-overlapping edits against the original block."""
        spans: list[tuple[int, int, str]] = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            source = edit.get("from")
            replacement = edit.get("to")
            if not isinstance(source, str) or not source or not isinstance(replacement, str):
                continue
            start = text.find(source)
            if start < 0 or text.find(source, start + 1) >= 0:
                continue
            end = start + len(source)
            if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in spans):
                continue
            spans.append((start, end, replacement))

        optimized = text
        for start, end, replacement in sorted(spans, reverse=True):
            optimized = optimized[:start] + replacement + optimized[end:]
        return optimized

    @staticmethod
    def _keep_exact_segments(text: str, segments: list[Any]) -> str:
        """Return only unique, non-overlapping spans copied verbatim from *text*."""
        spans: list[tuple[int, int]] = []
        for segment in segments:
            if not isinstance(segment, str) or not segment:
                continue
            start = text.find(segment)
            # Ambiguous short matches are unsafe: require the model to provide
            # enough context for a unique exact span.
            if start < 0 or text.find(segment, start + 1) >= 0:
                continue
            end = start + len(segment)
            if any(start < old_end and end > old_start for old_start, old_end in spans):
                continue
            spans.append((start, end))
        return "\n\n".join(text[start:end].strip() for start, end in sorted(spans) if text[start:end].strip())

    @staticmethod
    def _drop_exact_segments(text: str, segments: list[Any]) -> str:
        """Delete only unique, non-overlapping spans copied verbatim from *text*."""
        spans: list[tuple[int, int]] = []
        for segment in segments:
            if not isinstance(segment, str) or not segment:
                continue
            start = text.find(segment)
            if start < 0 or text.find(segment, start + 1) >= 0:
                continue
            end = start + len(segment)
            if any(start < old_end and end > old_start for old_start, old_end in spans):
                continue
            spans.append((start, end))

        filtered = text
        for start, end in sorted(spans, reverse=True):
            filtered = filtered[:start] + filtered[end:]
        return re.sub(r"\n{3,}", "\n\n", filtered).strip()

    @classmethod
    def _drop_obvious_normal_work_noise(
        cls,
        text: str,
        profile: Mapping[str, object] | None = None,
    ) -> str:
        """Remove obvious recording-pipeline self-test chatter from normal work notes."""
        if not text.strip():
            return ""
        profile = profile or {}
        custom = str(profile.get("custom_description", ""))
        if re.search(r"(录音|转写|筛选|Obsidian|笔记|耗时|速度|链路).*(需求|缺陷|bug|任务|待办|项目|方案)", custom, re.I):
            return text.strip()

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        kept: list[str] = []
        for paragraph in paragraphs:
            if cls._is_obvious_normal_work_noise(paragraph):
                continue
            kept.append(paragraph)
        return "\n\n".join(kept).strip()

    @staticmethod
    def _is_obvious_normal_work_noise(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text)
        if not normalized:
            return True
        meta_terms = [
            "录音", "录好音", "转写", "筛选", "文字优化", "优化筛选", "Obsidian",
            "最终笔记", "原文笔记", "后台", "处理链路", "链路耗时", "整体耗时",
            "下载转写", "耗时", "用了多久", "多长时间", "够不够快", "足够快",
            "显示出文字", "看到最终文字",
        ]
        intent_terms = [
            "测试", "试试", "看一下", "看看", "会不会", "是否生效", "能不能",
            "等测试完", "这次", "这段话", "照着念", "录下来", "顺便确认",
        ]
        action_terms = [
            "需求", "缺陷", "bug", "任务", "待办", "修复", "开发", "实现", "改成",
            "增加", "支持", "排查", "上线", "部署", "接口", "代码", "报错", "异常",
            "问题是", "方案", "风险", "负责人", "截止",
        ]
        meta_hits = sum(1 for term in meta_terms if term in normalized)
        intent_hits = sum(1 for term in intent_terms if term in normalized)
        has_action = any(term.lower() in normalized.lower() for term in action_terms)
        if meta_hits >= 2 and intent_hits >= 1 and not has_action:
            return True
        if meta_hits >= 3 and re.search(r"(耗时|用了多久|够不够快|足够快|是否生效|会不会.*去掉)", normalized):
            return True
        return False

    @classmethod
    def _fallback_keep_work_sentences(
        cls,
        text: str,
        profile: Mapping[str, object] | None = None,
    ) -> str:
        """Recover clear work sentences when the model drops everything."""
        profile = profile or {}
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        kept: list[str] = []
        for paragraph in paragraphs:
            for sentence in cls._split_sentences(paragraph):
                if cls._looks_like_real_work_sentence(sentence, profile):
                    kept.append(sentence.strip())
        return "\n\n".join(item for item in kept if item).strip()

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        pieces = [match.group(0).strip() for match in re.finditer(r"[^。！？!?；;]+[。！？!?；;]?", text)]
        return [piece for piece in pieces if piece]

    @staticmethod
    def _looks_like_real_work_sentence(text: str, profile: Mapping[str, object] | None = None) -> bool:
        normalized = re.sub(r"\s+", "", text)
        if not normalized:
            return False
        lower = normalized.lower()
        real_markers = [
            "修复",
            "解决",
            "排查",
            "实现",
            "新增",
            "增加",
            "支持",
            "开发",
            "上线",
            "部署",
            "改造",
            "重构",
            "联调",
            "对接",
            "写入",
            "显示到",
            "记录到",
            "同步到",
            "提交",
            "合并",
            "发布",
            "恢复",
            "迁移",
            "导出",
            "导入",
            "统计",
            "配置",
            "适配",
            "清理",
            "调整",
            "修改",
            "回退",
            "验证",
            "bug",
            "问题",
            "需求",
            "方案",
            "待办",
            "任务",
            "接口",
            "代码",
            "任务详情",
        ]
        meta_markers = [
            "测试",
            "试试",
            "看看",
            "会不会",
            "多久",
            "耗时",
            "Obsidian",
            "录音",
            "转写",
            "筛选",
            "最终文字",
            "链路",
            "速度",
            "后台",
            "下载",
            "笔记",
        ]
        has_real = any(marker.lower() in lower for marker in real_markers)
        has_meta = any(marker.lower() in lower for marker in meta_markers)
        if has_real and not (has_meta and not re.search(r"(修复|解决|排查|实现|新增|增加|支持|开发|上线|部署|改造|重构|联调|对接|写入|显示到|记录到|同步到|提交|合并|发布|恢复|迁移|导出|导入|统计|配置|适配|清理|调整|修改|回退|验证|bug|问题|需求|方案|待办|任务|接口|代码|任务详情)", lower)):
            return True
        return False

    def _completion(
        self,
        instruction: str,
        transcript: str,
        schema_name: str,
        properties: dict[str, Any],
        required: list[str],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        request_max_tokens = max_tokens or self.max_tokens
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": request_max_tokens,
            # The deployed DeepSeek v4 proxy supports JSON mode but can hang
            # on OpenAI's stricter json_schema response format. The response
            # is still validated against the expected shape by each caller.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": transcript},
            ],
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AI-Recording-Bean/0.5.11",
            },
        )
        if self.api_key:
            request.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429:
                retry_after = self.rate_limit_delay_seconds
                try:
                    retry_after = max(retry_after, float(exc.headers.get("Retry-After", "0")))
                except (TypeError, ValueError):
                    pass
                raise _RetryableContentFilterError(
                    "AI服务并发繁忙（HTTP 429），自动重试后仍未恢复。",
                    retry_after_seconds=retry_after,
                ) from exc
            if 500 <= exc.code < 600:
                raise _RetryableContentFilterError(
                    "AI服务暂时异常（HTTP %d）。" % exc.code
                ) from exc
            raise ContentFilterError("工作内容筛选服务返回 HTTP %d：%s" % (exc.code, detail)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _RetryableContentFilterError("无法连接工作内容筛选服务：%s" % exc) from exc
        try:
            response_payload = json.loads(body)
            choice = response_payload["choices"][0]
            finish_reason = choice.get("finish_reason")
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise _RetryableContentFilterError("AI服务返回结构异常，已自动重试。") from exc
        if finish_reason == "length":
            raise _RetryableContentFilterError(
                "AI模型输出达到 %d tokens 上限，自动扩大额度后仍未完成。"
                % request_max_tokens,
                increase_tokens=True,
            )
        if content is None or (isinstance(content, str) and not content.strip()):
            raise _RetryableContentFilterError(
                "AI模型返回空内容，已自动重试。",
                increase_tokens=True,
            )
        try:
            result_payload = self._parse_model_json(content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _RetryableContentFilterError("AI模型没有返回有效JSON，已自动重试。") from exc
        missing = [name for name in required if name not in result_payload]
        if missing:
            raise _RetryableContentFilterError(
                "AI模型返回结果缺少字段：%s。" % "、".join(missing)
            )
        return result_payload

    @staticmethod
    def _parse_model_json(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        text = str(value).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("模型没有返回 JSON 对象")
        return parsed
