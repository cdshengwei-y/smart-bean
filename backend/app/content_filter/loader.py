"""Load the configured semantic work-content filter."""

from __future__ import annotations

import importlib
import os

from .contract import ContentFilterError, WorkContentFilter


DEFAULT_FACTORY = (
    "app.content_filter.openai_compatible:OpenAICompatibleWorkContentFilter"
)


def load_content_filter(factory_path: str | None = None) -> WorkContentFilter:
    target = (factory_path or os.getenv("WORK_FILTER_FACTORY", DEFAULT_FACTORY)).strip()
    try:
        module_name, attribute_name = target.split(":", 1)
        factory = getattr(importlib.import_module(module_name), attribute_name)
        instance = factory()
    except ContentFilterError:
        raise
    except Exception as exc:
        raise ContentFilterError("无法加载工作内容筛选器 %s：%s" % (target, exc)) from exc
    if not isinstance(instance, WorkContentFilter):
        raise ContentFilterError("工作内容筛选器不符合 WorkContentFilter 接口：%s" % target)
    return instance

