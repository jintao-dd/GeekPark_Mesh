"""终答流式回调总线（方案 B 端到端真流式）。

用法与 supervisor/progress.py 类似，但用普通全局 + 线程锁而非 ContextVar，
因为答案生成可能在 supervisor 线程池的子线程里执行，ContextVar 不跨线程继承。

关键设计（防「出来后又重生成」）：
- feishu_bot 注册 on_delta 回调后，默认**不推送**；
- 只有 supervisor 最终 mouth（speak / synthesize_work）调用 begin_final_stream()
  期间，llm.call(task=answer) 才会走流式并 emit；
- 中间稿（ask.published 的 answer_question、文档草稿 speak 等）走非流式，
  不会抢写飞书卡片。
"""
from __future__ import annotations

import threading
from typing import Callable

_DeltaCb = Callable[[str, str], None]  # (delta, accumulated) -> None

_LOCK = threading.Lock()
_CB: _DeltaCb | None = None
_ENABLED = False
_FINAL = False  # 仅终答阶段为 True


def set_delta_callback(cb: _DeltaCb | None) -> None:
    global _CB, _ENABLED, _FINAL
    with _LOCK:
        _CB = cb
        _ENABLED = cb is not None
        _FINAL = False  # 注册后默认关闭，等 mouth 显式打开


def reset_delta_callback() -> None:
    global _CB, _ENABLED, _FINAL
    with _LOCK:
        _CB = None
        _ENABLED = False
        _FINAL = False


def begin_final_stream() -> None:
    """终答 mouth 入口：打开推送闸门。"""
    global _FINAL
    with _LOCK:
        _FINAL = True


def end_final_stream() -> None:
    """终答 mouth 出口：关闭推送闸门。"""
    global _FINAL
    with _LOCK:
        _FINAL = False


def stream_active() -> bool:
    """llm.call 用它判断是否需要走流式路径（需回调已注册且处于终答阶段）。"""
    with _LOCK:
        return _ENABLED and _FINAL and _CB is not None


def emit_delta(delta: str, accumulated: str) -> None:
    with _LOCK:
        cb = _CB
        final = _FINAL
    if not cb or not final or not delta:
        return
    try:
        cb(str(delta), str(accumulated))
    except Exception:
        # 流式推送失败绝不能影响答案生成
        pass
