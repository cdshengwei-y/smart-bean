"""Create an Obsidian-compatible Markdown note from normalized text."""

from __future__ import annotations

import json
from pathlib import Path

from .content_filter.contract import WorkContentFilterResult
from .transcription.contract import TranscriptionRequest, TranscriptionResult


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_markdown(
    request: TranscriptionRequest,
    result: TranscriptionResult,
    filtered: WorkContentFilterResult,
) -> str:
    recording_name = Path(request.source_object_name or request.audio_path.name).name
    title = Path(recording_name).stem
    language = result.language or request.language or ""
    duration = "" if result.duration_seconds is None else "%.2f" % result.duration_seconds
    device_id = request.metadata.get("device_id", "legacy")
    has_text = filtered.has_work_content
    recording_scene = filtered.metadata.get("recording_scene", "work")
    scene_name = {"meeting": "会议内容", "all": "全部内容"}.get(recording_scene, "普通工作内容")
    transcription_status = {
        "partial": "转写中",
        "completed": "已完成",
    }.get(filtered.metadata.get("transcription_status", "completed"), "已完成")
    ai_status = {
        "waiting": "等待处理",
        "optimizing": "正在优化",
        "filtering": "正在筛选",
        "failed": "处理失败，等待重试",
        "skipped": "已跳过（AI 筛选关闭）",
        "completed": "已完成",
    }.get(filtered.metadata.get("ai_status", "completed"), "已完成")

    lines = [
        "---",
        "录音文件: %s" % _yaml_string(recording_name),
        "设备 ID: %s" % _yaml_string(device_id),
        "云端路径: %s" % _yaml_string(request.source_object_name),
        "语言: %s" % _yaml_string(language),
        "时长秒: %s" % duration,
        "转写模型: %s" % _yaml_string(result.model_name),
        "内容筛选模型: %s" % (
            "未使用（AI 筛选关闭）"
            if filtered.model_name == "pending"
            else _yaml_string(filtered.model_name)
        ),
        "转写状态: %s" % (transcription_status if result.text.strip() else "未识别到文字"),
        "AI处理状态: %s" % ai_status,
        "文字优化: %s" % ("已开启" if filtered.metadata.get("optimization_enabled") == "true" else "已关闭"),
        "工作筛选: %s" % ("已开启" if filtered.metadata.get("filter_enabled") == "true" else "已关闭"),
        "记录场景: %s" % scene_name,
        "---",
        "",
        "# %s" % title,
        "",
        "## %s" % (
            scene_name if filtered.metadata.get("filter_enabled") == "true" else "完整文字"
        ),
        "",
    ]
    if has_text:
        lines.extend((filtered.text.strip(), ""))
    return "\n".join(lines)
