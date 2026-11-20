"""Backend tests."""

# AI 流程相关的测试默认开启 AI 筛选（RECORDER_AI_FILTER=1），
# 使 mark_note_ready 走 ai_pending 队列断言；禁用场景用
# patch.dict(os.environ, {"RECORDER_AI_FILTER": "0"}) 单独覆盖。
import os

os.environ.setdefault("RECORDER_AI_FILTER", "1")
