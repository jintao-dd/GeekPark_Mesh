"""后台任务调度：Web 入队 / 同进程 inline 执行 / 独立 worker 认领。

MESH_JOB_INLINE=1（默认）：claim 后本进程起线程（本地开发）。
MESH_JOB_INLINE=0：只写入 mesh_jobs（_executor=pending），由 job_worker 执行。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable


def jobs_inline() -> bool:
    v = (os.environ.get("MESH_JOB_INLINE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def mark_pending(kind: str, key: str, defaults: dict) -> None:
    from . import job_store

    st = job_store.get(kind, key, defaults)
    if not st.get("running"):
        return
    st["_executor"] = "pending"
    job_store.put(kind, key, st)


def mark_executor(kind: str, key: str, defaults: dict, holder: str) -> bool:
    """将 pending 任务标记为某执行者；已被别人拿走则 False。"""
    from . import job_store

    st = job_store.get(kind, key, defaults)
    if not st.get("running"):
        return False
    cur = st.get("_executor")
    if cur not in (None, "", "pending"):
        return False
    st["_executor"] = holder
    job_store.put(kind, key, st)
    # 再读确认（单 worker 足够；多副本时仍可能竞态，compose 保持 replicas=1）
    st2 = job_store.get(kind, key, defaults)
    return st2.get("_executor") == holder and bool(st2.get("running"))


def spawn_after_claim(
    kind: str,
    key: str,
    defaults: dict,
    target: Callable[..., Any],
    args: tuple = (),
) -> None:
    """try_claim 成功后调用：inline 起线程，否则标 pending 等 worker。"""
    from . import job_store

    if jobs_inline():
        st = job_store.get(kind, key, defaults)
        st["_executor"] = "inline"
        job_store.put(kind, key, st)
        threading.Thread(target=target, args=args, daemon=True).start()
    else:
        mark_pending(kind, key, defaults)
