"""Conservative deterministic cleanup for common ASR repetition artifacts."""
from __future__ import annotations
import re

def clean_asr_text(text: str) -> str:
    value = re.sub(r"[ \t]+", " ", text).strip()
    value = re.sub(r"([，。！？；、,.!?;])\1{1,}", r"\1", value)
    # Compress 3+ adjacent repeats. Requiring three copies preserves ordinary
    # two-word emphasis such as “快快” while fixing ASR hallucination loops.
    value = re.sub(r"(.{2,20}?)(?:\1){2,}", r"\1", value)
    value = remove_discourse_fillers(value)
    return value


def remove_discourse_fillers(text: str) -> str:
    """Remove standalone ASR filler words while preserving lexical uses."""
    filler = r"(?:嗯|呃|额)"
    sequence = rf"{filler}(?:[ \t]*{filler})*"
    punctuation = r"[，,、；;。！？!?]"
    # At the beginning of a sentence, consume the separator that follows the
    # filler as well: “嗯，继续” becomes “继续”.
    value = re.sub(rf"^{sequence}[ \t]*{punctuation}?[ \t]*", "", text)
    value = re.sub(rf"([。！？!?])\s*{sequence}[ \t]*{punctuation}?[ \t]*(?=[^嗯呃额]|$)", r"\1", value)
    value = re.sub(rf"([，,、；;])\s*{sequence}[ \t]*[，,、；;]", r"\1", value)
    value = re.sub(rf"([，,、；;])\s*{sequence}[ \t]*[。！？!?]", r"。", value)
    return value
