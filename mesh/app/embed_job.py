"""全局 Embedding 回填任务（mesh_jobs kind=embed）。"""
from __future__ import annotations

from . import chunk_index, db, embeddings, job_runtime, job_store

KIND = "embed"
KEY = "global"


def _defaults() -> dict:
    return {
        "running": False,
        "done": False,
        "error": None,
        "token": 0,
        "message": "",
        "added": 0,
    }


def get_state() -> dict:
    return job_store.get(KIND, KEY, _defaults())


def start(*, force: bool = False) -> dict:
    if not embeddings.is_configured():
        raise RuntimeError("未配置 MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL")
    claimed = job_store.try_claim(
        KIND,
        KEY,
        _defaults(),
        {"running": True, "done": False, "error": None, "message": "回填中…", "added": 0},
        force=force,
    )
    if not claimed:
        return get_state()
    token = int(claimed.get("token") or 0)
    job_runtime.spawn_after_claim(KIND, KEY, _defaults(), _run, (token,))
    return get_state()


def _run(tok: int) -> None:
    defaults = _defaults()
    con = db.connect()
    try:
        n_before = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
        added = chunk_index.embed_all_missing(con)
        with db.write_lock():
            db.commit_retry(con)
        n_after = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
        if not job_store.is_current(KIND, KEY, tok, defaults):
            return
        out = {
            "running": False,
            "done": True,
            "token": tok,
            "message": f"完成 +{added}",
            "added": added,
            "embeddings_before": n_before,
            "embeddings_after": n_after,
            "error": None,
            "_executor": "done",
        }
        if added <= 0 and n_after <= n_before and embeddings.last_error():
            out["error"] = embeddings.last_error()
            out["message"] = "无新增（见 error）"
        job_store.put(KIND, KEY, out)
    except Exception as e:
        if job_store.is_current(KIND, KEY, tok, defaults):
            job_store.put(
                KIND,
                KEY,
                {
                    "running": False,
                    "done": False,
                    "token": tok,
                    "error": str(e)[:300],
                    "message": "失败",
                    "_executor": "done",
                },
            )
    finally:
        con.close()
