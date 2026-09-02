"""Embedding 回填：按 issue slug 异步补向量（mesh_jobs kind=embed）。"""
from __future__ import annotations

from . import chunk_index, db, embeddings, job_runtime, job_store

KIND = "embed"
GLOBAL_KEY = "global"


def _defaults(key: str = GLOBAL_KEY) -> dict:
    return {
        "slug": None if key == GLOBAL_KEY else key,
        "running": False,
        "done": False,
        "error": None,
        "token": 0,
        "message": "",
        "added": 0,
        "embedding_done": 0,
        "embedding_total": 0,
    }


def get_state(key: str = GLOBAL_KEY) -> dict:
    return job_store.get(KIND, key, _defaults(key))


def _needs_embed(info: dict) -> bool:
    return info.get("embedding_status") in ("pending", "partial", "failed")


def ensure_for_slug(slug: str, *, by: str = "publish", force: bool = False) -> dict:
    """保证该期 embedding 意图已落库，并尽量入队（幂等）。

    持久化两层：
    1. issues.embedding_status=pending（与 publish 同事务写入，启动时会再扫）
    2. mesh_jobs kind=embed key=slug（执行租约；重复提交不重复跑）
    """
    slug = (slug or "").strip()
    if not slug:
        raise ValueError("slug required")

    defaults = _defaults(slug)
    job_store.reclaim_stale(KIND, slug, defaults)

    con = db.connect()
    info: dict = {}
    try:
        row = con.execute("SELECT id, status FROM issues WHERE slug=?", (slug,)).fetchone()
        if not row or row["status"] != "published":
            return {
                "ok": True,
                "persisted": False,
                "queued": False,
                "skipped": True,
                "idempotent": True,
                "message": "未上线，跳过",
            }
        if not embeddings.is_configured():
            info = db.refresh_issue_embedding_status(con, row["id"])
            with db.write_lock():
                db.commit_retry(con)
            return {
                "ok": True,
                "persisted": True,
                "queued": False,
                "skipped": True,
                "idempotent": True,
                **info,
            }
        info = db.refresh_issue_embedding_status(con, row["id"])
        if _needs_embed(info) or force:
            info = db.refresh_issue_embedding_status(con, row["id"], status="pending")
        with db.write_lock():
            db.commit_retry(con)
    finally:
        con.close()

    persisted = _needs_embed(info) or info.get("embedding_status") == "running" or force
    if info.get("embedding_status") == "completed" and not force:
        return {
            "ok": True,
            "persisted": True,
            "queued": False,
            "idempotent": True,
            "already_done": True,
            **info,
        }

    cur = get_state(slug)
    if cur.get("running") and not force:
        return {
            "ok": True,
            "persisted": True,
            "queued": True,
            "idempotent": True,
            "already_running": True,
            "message": cur.get("message") or "已在补齐",
            **info,
        }

    new_st = _defaults(slug)
    new_st.update({"message": f"排队 embedding（{by}）", "by": by})
    claimed = job_store.try_claim(KIND, slug, defaults, new_st, force=force)
    if not claimed:
        job_store.reclaim_stale(KIND, slug, defaults)
        claimed = job_store.try_claim(KIND, slug, defaults, new_st, force=False)

    if not claimed:
        cur = get_state(slug)
        return {
            "ok": True,
            "persisted": persisted,
            "queued": bool(cur.get("running")),
            "idempotent": True,
            "claim_deferred": True,
            "message": cur.get("message") or "任务已在队列",
            **info,
        }

    token = int(claimed.get("token") or 0)
    job_runtime.spawn_after_claim(KIND, slug, defaults, _run, (slug, token))
    st = get_state(slug)
    return {
        "ok": True,
        "persisted": True,
        "queued": True,
        "idempotent": False,
        "token": token,
        **info,
        **{k: st.get(k) for k in ("running", "message")},
    }


def enqueue_for_slug(slug: str, *, by: str = "publish", force: bool = False) -> dict:
    """兼容旧调用：等同 ensure_for_slug。"""
    return ensure_for_slug(slug, by=by, force=force)


def start(*, force: bool = False) -> dict:
    """全局回填：依次处理所有缺向量的已发布期。"""
    if not embeddings.is_configured():
        raise RuntimeError("未配置 MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL")
    claimed = job_store.try_claim(
        KIND,
        GLOBAL_KEY,
        _defaults(GLOBAL_KEY),
        {
            "running": True,
            "done": False,
            "error": None,
            "message": "全局回填排队中…",
            "added": 0,
        },
        force=force,
    )
    if not claimed:
        return get_state(GLOBAL_KEY)
    token = int(claimed.get("token") or 0)
    job_runtime.spawn_after_claim(GLOBAL_KEY, GLOBAL_KEY, _defaults(GLOBAL_KEY), _run, (GLOBAL_KEY, token))
    return get_state(GLOBAL_KEY)


def _embed_slug_work(con, slug: str, issue_id: int) -> dict:
    """同步补齐单期缺失向量，返回 {added, total, done, status, error}。"""
    db.refresh_issue_embedding_status(con, issue_id, status="running", running=True)
    with db.write_lock():
        db.commit_retry(con)

    total_added = 0
    last_err = ""
    for _ in range(200):
        added = chunk_index.embed_missing_for_slug(con, slug)
        if added > 0:
            total_added += added
            with db.write_lock():
                db.commit_retry(con)
            db.refresh_issue_embedding_status(con, issue_id, running=True)
            continue
        last_err = embeddings.last_error() or ""
        total, done = chunk_index.embedding_counts_for_slug(con, slug)
        if done >= total:
            break
        if last_err:
            break

    total, done = chunk_index.embedding_counts_for_slug(con, slug)
    if done >= total:
        info = db.refresh_issue_embedding_status(con, issue_id, status="completed")
        return {
            "added": total_added,
            "total": info.get("embedding_total", total),
            "done": info.get("embedding_done", done),
            "status": "completed",
            "error": None,
        }
    if done > 0:
        info = db.refresh_issue_embedding_status(
            con, issue_id, status="partial", error=last_err[:300] if last_err else None,
        )
        return {
            "added": total_added,
            "total": info.get("embedding_total", total),
            "done": info.get("embedding_done", done),
            "status": "partial",
            "error": last_err[:300] if last_err else None,
        }
    info = db.refresh_issue_embedding_status(
        con, issue_id, status="failed", error=last_err[:300] if last_err else "无新增",
    )
    return {
        "added": total_added,
        "total": info.get("embedding_total", total),
        "done": info.get("embedding_done", done),
        "status": "failed",
        "error": last_err[:300] if last_err else "无新增",
    }


def _run(key: str, tok: int) -> None:
    if key == GLOBAL_KEY:
        _run_global(tok)
    else:
        _run_slug(key, tok)


def _run_slug(slug: str, tok: int) -> None:
    defaults = _defaults(slug)
    if not job_store.is_current(KIND, slug, tok, defaults):
        return
    con = db.connect()
    issue_id = None
    try:
        row = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
        if not row:
            job_store.put(
                KIND,
                slug,
                {
                    "running": False,
                    "done": True,
                    "token": tok,
                    "message": "期不存在",
                    "_executor": "done",
                },
            )
            return
        issue_id = row["id"]
        result = _embed_slug_work(con, slug, issue_id)
        with db.write_lock():
            db.commit_retry(con)
        if not job_store.is_current(KIND, slug, tok, defaults):
            return
        final_done = result["status"] == "completed"
        job_store.put(
            KIND,
            slug,
            {
                "running": False,
                "done": final_done,
                "token": tok,
                "message": f"{result['done']}/{result['total']}",
                "added": result["added"],
                "embedding_done": result["done"],
                "embedding_total": result["total"],
                "error": result["error"],
                "_executor": "done",
            },
        )
    except Exception as e:
        if issue_id is not None:
            try:
                db.refresh_issue_embedding_status(con, issue_id, status="failed", error=str(e)[:300])
                with db.write_lock():
                    db.commit_retry(con)
            except Exception:
                pass
        if job_store.is_current(KIND, slug, tok, defaults):
            job_store.put(
                KIND,
                slug,
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


def _run_global(tok: int) -> None:
    defaults = _defaults(GLOBAL_KEY)
    con = db.connect()
    try:
        slugs = db.issues_needing_embedding(con)
        with db.write_lock():
            db.commit_retry(con)
        total_added = 0
        processed = 0
        if not slugs:
            added = chunk_index.embed_all_missing(con)
            with db.write_lock():
                db.commit_retry(con)
            if not job_store.is_current(KIND, GLOBAL_KEY, tok, defaults):
                return
            out = {
                "running": False,
                "done": True,
                "token": tok,
                "message": f"完成 +{added}",
                "added": added,
                "error": None,
                "_executor": "done",
            }
            if added <= 0 and embeddings.last_error():
                out["error"] = embeddings.last_error()
                out["message"] = "无新增（见 error）"
                out["done"] = False
            job_store.put(KIND, GLOBAL_KEY, out)
            return

        for slug in slugs:
            if not job_store.is_current(KIND, GLOBAL_KEY, tok, defaults):
                return
            row = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
            if not row:
                continue
            job_store.put(
                KIND,
                GLOBAL_KEY,
                {
                    "running": True,
                    "done": False,
                    "token": tok,
                    "message": f"处理 {slug}…",
                    "added": total_added,
                    "_executor": "done",
                },
            )
            result = _embed_slug_work(con, slug, row["id"])
            with db.write_lock():
                db.commit_retry(con)
            total_added += int(result.get("added") or 0)
            processed += 1

        if not job_store.is_current(KIND, GLOBAL_KEY, tok, defaults):
            return
        job_store.put(
            KIND,
            GLOBAL_KEY,
            {
                "running": False,
                "done": True,
                "token": tok,
                "message": f"完成 {processed} 期 +{total_added}",
                "added": total_added,
                "error": None,
                "_executor": "done",
            },
        )
    except Exception as e:
        if job_store.is_current(KIND, GLOBAL_KEY, tok, defaults):
            job_store.put(
                KIND,
                GLOBAL_KEY,
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
