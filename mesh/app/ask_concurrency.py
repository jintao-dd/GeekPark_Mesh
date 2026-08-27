"""AI 问答并发控制：限制同时进行的 LLM 调用，避免打满上游与长时间占库。"""
from __future__ import annotations

import os
import threading

_LLM_CONCURRENCY = max(1, int(os.environ.get("MESH_ASK_LLM_CONCURRENCY", "4") or "4"))
_LLM_QUEUE_TIMEOUT = max(1, int(os.environ.get("MESH_ASK_QUEUE_TIMEOUT", "120") or "120"))
_LLM_SEM = threading.Semaphore(_LLM_CONCURRENCY)


class AskBusyError(Exception):
    """排队超时：当前并发已满。"""


class llm_slot:
    """获取 LLM 并发槽；退出时释放。"""

    def __enter__(self):
        if not _LLM_SEM.acquire(timeout=_LLM_QUEUE_TIMEOUT):
            raise AskBusyError("当前问答较多，请稍后再试")
        return self

    def __exit__(self, exc_type, exc, tb):
        _LLM_SEM.release()
        return False
