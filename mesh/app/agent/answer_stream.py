"""终答流式回调总线（方案 B 端到端真流式）。

用法与 supervisor/progress.py 类似，但用普通全局 + 线程锁而非 ContextVar，
因为答案生成可能在 supervisor 线程池的子线程里执行，ContextVar 不跨线程继承。

feishu_bot 在开启 FEISHU_TRUE_STREAM 时注册 on_delta 回调；
llm.call(task="answer", 非 json_mode) 会在流式过程中逐 chunk emit_delta。
"""
from __future__ import annotations

import threading
from typing import Callable

_DeltaCb = Callable[[str, str], None]  # (delta, accumulated) -> None

_LOCK = threading.Lock()
_CB: _DeltaCb | None = None
_ENABLED = False


def set_delta_callback(cb: _DeltaCb | None) -> None:
    global _CB, _ENABLED
    with _LOCK:
        _CB = cb
        _ENABLED = cb is not None


def reset_delta_callback() -> None:
    global _CB, _ENABLED
    with _LOCK:
        _CB = None
        _ENABLED = False


def stream_active() -> bool:
    """llm.call 用它判断是否需要走流式路径。"""
    with _LOCK:
        return _ENABLED and _CB is not None


def emit_delta(delta: str, accumulated: str) -> None:
    with _LOCK:
        cb = _CB
    if not cb or not delta:
        return
    try:
        cb(str(delta), str(accumulated))
    except Exception:
        # 流式推送失败绝不能影响答案生成
        pass
