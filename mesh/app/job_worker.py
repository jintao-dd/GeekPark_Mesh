"""独立后台 Worker：认领 mesh_jobs 中 pending 的 pipeline/preview/edm/embed。

用法：
  MESH_JOB_INLINE=0 python -m app.job_worker

docker-compose 中 mesh-worker 服务使用本入口。保持 replicas=1。
"""
from __future__ import annotations

import json
import os
import signal
import time
import traceback

_STOP = False


def _handle_stop(signum, frame):  # noqa: ARG001
    global _STOP
    _STOP = True
    print(f"[mesh-worker] signal {signum}, stopping…", flush=True)


def _holder() -> str:
    return f"worker:{os.getpid()}"


def _tick_once() -> int:
    from . import db, embed_job, edm_job, job_runtime, job_store, pipeline, preview_job

    handled = 0
    holder = _holder()
    con = db.connect()
    try:
        job_store.ensure_table(con)
        rows = con.execute(
            "SELECT job_kind, job_key, payload_json FROM mesh_jobs WHERE running=1"
        ).fetchall()
    finally:
        con.close()

    for row in rows:
        if _STOP:
            break
        kind = row["job_kind"]
        key = row["job_key"]
        try:
            payload = json.loads(row["payload_json"] or "{}") or {}
        except Exception:
            payload = {}
        executor = payload.get("_executor")
        if executor not in (None, "", "pending"):
            continue

        if kind == "pipeline":
            defaults = pipeline._defaults(key)
            if not job_runtime.mark_executor(kind, key, defaults, holder):
                continue
            token = int(job_store.get(kind, key, defaults).get("token") or 0)
            print(f"[mesh-worker] pipeline {key} token={token}", flush=True)
            try:
                pipeline._run(key, token)
            except Exception:
                traceback.print_exc()
            handled += 1
        elif kind == "preview":
            defaults = preview_job._defaults(key)
            if not job_runtime.mark_executor(kind, key, defaults, holder):
                continue
            st = job_store.get(kind, key, defaults)
            token = int(st.get("token") or 0)
            username = st.get("_username") or "mesh-worker"
            print(f"[mesh-worker] preview {key} token={token}", flush=True)
            try:
                preview_job._run(key, username, token)
            except Exception:
                traceback.print_exc()
            handled += 1
        elif kind == "edm":
            defaults = edm_job._defaults(key)
            if not job_runtime.mark_executor(kind, key, defaults, holder):
                continue
            st = job_store.get(kind, key, defaults)
            token = int(st.get("token") or 0)
            by = st.get("by") or "worker"
            retries = int(st.get("retries") or 2)
            print(f"[mesh-worker] edm {key} token={token}", flush=True)
            try:
                edm_job._auto_send(key, by, retries, token)
            except Exception:
                traceback.print_exc()
            handled += 1
        elif kind == "embed":
            defaults = embed_job._defaults(key)
            if not job_runtime.mark_executor(kind, key, defaults, holder):
                continue
            st = job_store.get(kind, key, defaults)
            token = int(st.get("token") or 0)
            print(f"[mesh-worker] embed {key} token={token}", flush=True)
            try:
                embed_job._run(key, token)
            except Exception:
                traceback.print_exc()
            handled += 1

    return handled


def main() -> None:
    from . import job_runtime, job_store

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    poll = max(0.5, float(os.environ.get("MESH_JOB_POLL_SEC", "1.5") or "1.5"))
    print(
        f"[mesh-worker] start pid={os.getpid()} poll={poll}s "
        f"MESH_JOB_INLINE={1 if job_runtime.jobs_inline() else 0}",
        flush=True,
    )
    if job_runtime.jobs_inline():
        print(
            "[mesh-worker] warn: MESH_JOB_INLINE=1 — Web 也会跑线程；生产请设 INLINE=0",
            flush=True,
        )

    try:
        n_dead = job_store.reclaim_dead_executors(_holder())
        if n_dead:
            print(f"[mesh-worker] reclaimed {n_dead} dead-executor job(s)", flush=True)
    except Exception:
        traceback.print_exc()

    while not _STOP:
        try:
            n = _tick_once()
            if n:
                continue
        except Exception:
            traceback.print_exc()
        time.sleep(poll)
    print("[mesh-worker] bye", flush=True)


if __name__ == "__main__":
    main()
