"""终答流式回调总线（方案 B 端到端真流式）。

用法与 supervisor/progress.py 类似，但用普通全局 + 线程锁而非 ContextVar，
因为答案生成可能在 supervisor 线程池的子线程里执行，ContextVar 不跨线程继承。

关键设计（防「出来后又重生成」）：
- feishu_bot 注册 on_delta 回调后，默认**不推送**；
- 只有 supervisor 最终 mouth（speak / synthesize_work）调用 begin_final_stream()
  期间，llm.call(task=answer) 才会走流式并 emit；
- 中间稿（ask.published 的 answer_question、文档草稿 speak 等）走非流式，
  不会抢写飞书卡片。

并发隔离（防「答案写进别人的卡片」）：
- 回调总线是**进程级单槽**（_CB），而飞书 job 池默认 32 并发，
  多个 job 同时注册会互相覆盖回调、互相 reset；
- 因此每个 job 注册时拿一个 token（key），并把 key 放到当前线程的 ContextVar；
- begin_final_stream() 会校验「当前线程的 key == 注册者的 key」，
  不匹配则**不开闸**——该 job 的答案退化为非流式（收尾整卡推送），
  而不是流式写进别的 job 的卡片；
- reset_delta_callback(token) 只清自己的回调，不会误清已被新 job 接管的槽位。
"""
from __future__ import annotations

import contextvars
import threading
import uuid
from typing import Callable

_DeltaCb = Callable[[str, str], None]  # (delta, accumulated) -> None

_LOCK = threading.Lock()
_CB: _DeltaCb | None = None
_CB_KEY = ""  # 当前回调归属的 job key
_TOKEN = ""  # 当前回调的注册 token（用于安全注销）
_ENABLED = False
_FINAL = False  # 仅终答阶段为 True

# 当前线程所属的流式 job key。feishu_bot 在 job 线程入口设置，
# supervisor/runtime 在同线程调用 begin_final_stream() 时自动读取。
_CURRENT_KEY: contextvars.ContextVar[str] = contextvars.ContextVar("answer_stream_key", default="")


def current_key() -> str:
    """当前线程的流式 job key（未设置时为空串）。"""
    return _CURRENT_KEY.get()


def set_current_key(key: str) -> None:
    """在 job 线程入口设置本线程的流式 key。"""
    _CURRENT_KEY.set(str(key or ""))


def set_delta_callback(cb: _DeltaCb | None, *, key: str = "") -> str:
    """注册回调，返回本次注册的 token。

    key 为该 job 的归属标识；begin_final_stream 会用它校验闸门归属，
    避免并发 job 之间串写飞书卡片。
    """
    global _CB, _CB_KEY, _TOKEN, _ENABLED, _FINAL
    tok = uuid.uuid4().hex
    with _LOCK:
        _CB = cb
        _CB_KEY = str(key or "")
        _TOKEN = tok
        _ENABLED = cb is not None
        _FINAL = False  # 注册后默认关闭，等 mouth 显式打开
    return tok


def reset_delta_callback(token: str = "") -> None:
    """注销回调。

    带 token 时只清自己注册的那一份；若槽位已被更新的 job 接管，
    则不动（避免旧 job 收尾时误清新 job 的回调）。
    """
    global _CB, _CB_KEY, _TOKEN, _ENABLED, _FINAL
    with _LOCK:
        if token and token != _TOKEN:
            return
        _CB = None
        _CB_KEY = ""
        _TOKEN = ""
        _ENABLED = False
        _FINAL = False


def begin_final_stream() -> bool:
    """终答 mouth 入口：打开推送闸门。

    校验当前线程的 key 与回调注册者是否一致；不一致（说明本 job 的
    回调已被并发 job 覆盖）则不开闸，该 job 退化为非流式。
    返回是否成功开闸。
    """
    global _FINAL
    with _LOCK:
        if _CB is None:
            return False
        if _CB_KEY and _CB_KEY != current_key():
            return False
        _FINAL = True
        return True


def end_final_stream() -> None:
    """终答 mouth 出口：关闭推送闸门。"""
    global _FINAL
    with _LOCK:
        _FINAL = False


def stream_active() -> bool:
    """llm.call 用它判断是否需要走流式路径（需回调已注册且处于终答阶段）。"""
    with _LOCK:
        if not (_ENABLED and _FINAL and _CB is not None):
            return False
        # 归属校验：只有回调注册者本人所在线程才允许流式
        return not (_CB_KEY and _CB_KEY != current_key())


def emit_delta(delta: str, accumulated: str) -> None:
    with _LOCK:
        cb = _CB
        final = _FINAL
        owned = not (_CB_KEY and _CB_KEY != current_key())
    if not cb or not final or not owned or not delta:
        return
    try:
        cb(str(delta), str(accumulated))
    except Exception:
        # 流式推送失败绝不能影响答案生成
        pass
