"""Load a transcription adapter without coupling the backend to one model."""

from __future__ import annotations

import importlib
from typing import Callable, cast

from .contract import Transcriber, TranscriberNotConfigured


DEFAULT_FACTORY = "app.transcription.local_model_adapter:create_transcriber"


def load_transcriber(factory_spec: str = DEFAULT_FACTORY) -> Transcriber:
    try:
        module_name, attribute_name = factory_spec.split(":", 1)
        module = importlib.import_module(module_name)
        factory = cast(Callable[[], object], getattr(module, attribute_name))
        transcriber = factory()
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise TranscriberNotConfigured("无法加载转写适配器 %s：%s" % (factory_spec, exc)) from exc

    if not isinstance(transcriber, Transcriber):
        raise TranscriberNotConfigured("转写适配器必须提供 provider_name 和 transcribe()。")
    return transcriber
