#!/usr/bin/env python3
"""Layer 2.1：Publish 前后 Agent 可见性（真实数据生命周期）。

验收硬条：
  Publish 前 — Agent 不得检索到仅存在于 draft 的标记内容
  Publish 后 — Agent 能检索到同一标记，并带 EvidenceRef

用法（mesh/ 目录）：
  # 本地临时库（默认）
  python eval/run_agent_publish_boundary_e2e.py

  # 指向已有库（谨慎；会创建/清理专用 slug）
  MESH_DB=/path/to.db python eval/run_agent_publish_boundary_e2e.py --reuse-env-db

不扩成全量万能脚本；不做飞书；默认不碰 prod。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SLUG = "agent-e2e-pub-boundary"
MARKER_PREFIX = "验收钉记"



@contextmanager
def _temp_db():
    from app import db, db_conn

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old_env = os.environ.get("MESH_DB")
    old_url = os.environ.get("MESH_DB_URL")
    old_path = db_conn.DB_PATH
    old_mesh_url = db_conn.MESH_DB_URL
    old_db_path = getattr(db, "DB_PATH", None)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    db_conn.DB_PATH = path
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = path
    db.init_db(seed=False)
    try:
        yield path
    finally:
        if old_env is None:
            os.environ.pop("MESH_DB", None)
        else:
            os.environ["MESH_DB"] = old_env
        if old_url is None:
            os.environ.pop("MESH_DB_URL", None)
        else:
            os.environ["MESH_DB_URL"] = old_url
        db_conn.DB_PATH = old_path
        db_conn.MESH_DB_URL = old_mesh_url or ""
        if old_db_path is not None:
            db.DB_PATH = old_db_path
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed_user(con) -> None:
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('agent_e2e','Agent E2E','x','viewer','编辑部','ou_agent_e2e')"
    )
    con.commit()


def _upsert_draft(con, marker: str) -> int:
    from app.relation_display import build_published_projection

    draft = {
        "keywords": {
            "groups": [
                {
                    "title": "E2E标记组",
                    "items": [
                        {
                            "name": marker,
                            "sub": "编辑部",
                            "rows": [
                                {
                                    "k": "摘要",
                                    "v": f"仅草稿可见标记 {marker} 用于 Publish 边界验收",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        "relations": [
            {
                "title": f"关系卡含 {marker}",
                "label": "已联动",
                "body": f"draft relation body {marker}",
                "decision_tier": "strong",
                "teams": ["编辑部"],
                "evidence": [{"item_id": 9001, "quote": marker}],
            }
        ],
    }
    # draft 期即使有 published_json 投影，reindex 也不应进 FTS（status=draft）
    pub_proj = build_published_projection(draft)
    con.execute("DELETE FROM issues WHERE slug=?", (SLUG,))
    con.execute(
        "INSERT INTO issues(slug, date_start, date_end, period_label, status, "
        "draft_json, published_json) VALUES (?,?,?,?,?,?,?)",
        (
            SLUG,
            "2026-09-01",
            "2026-09-07",
            "Agent E2E Pub",
            "draft",
            json.dumps(draft, ensure_ascii=False),
            json.dumps(pub_proj, ensure_ascii=False),
        ),
    )
    con.commit()
    return int(con.execute("SELECT id FROM issues WHERE slug=?", (SLUG,)).fetchone()["id"])


def _agent_ask(con, marker: str) -> dict:
    from app.agent.harness import run_harness

    return run_harness(
        con,
        {
            "text": f"请查找标记 {marker} 相关进展",
            "channel": "harness",
            "feishu_open_id": "ou_agent_e2e",
            "explicit_issue": SLUG,
        },
    )


def _seen_in_answer(r: dict, marker: str) -> bool:
    blob = (r.get("text") or "") + json.dumps(r.get("claim_bindings") or [], ensure_ascii=False)
    return marker in blob


def _publish(con, issue_id: int) -> None:
    from app import db
    from app.publish_lane import allow_published_write, write_publish_projection
    from app.relation_display import build_published_projection

    row = con.execute(
        "SELECT draft_json, date_start, date_end, period_label FROM issues WHERE id=?",
        (issue_id,),
    ).fetchone()
    draft = json.loads(row["draft_json"] or "{}")
    pub = build_published_projection(draft)
    if "keywords" in draft:
        pub["keywords"] = draft["keywords"]
    now = time.strftime("%Y-%m-%d %H:%M")
    with allow_published_write("agent_publish_boundary_e2e"):
        write_publish_projection(
            con,
            issue_id,
            pub_payload=json.dumps(pub, ensure_ascii=False),
            draft_payload=row["draft_json"] or "{}",
            published_at=now,
            updated_at=now,
            period_label=row["period_label"] or SLUG,
            date_end=row["date_end"] or "2026-09-07",
            date_start=row["date_start"] or "2026-09-01",
        )
    con.commit()
    db.reindex_issue(con, issue_id, items=False, rebuild_chunks=True)
    con.commit()


def _disable_embed_for_e2e() -> None:
    """E2E 不依赖远端 embedding；避免 tmesh embed timeout 拖死检索。不改产品默认。"""
    from app import embeddings

    embeddings.embed_one = lambda _t: []  # type: ignore[assignment]
    embeddings.is_configured = lambda: False  # type: ignore[assignment]


def run_once(con) -> dict:
    _disable_embed_for_e2e()
    marker = f"{MARKER_PREFIX}_{uuid.uuid4().hex[:8]}"
    # 可重入：清掉上次残留
    con.execute("DELETE FROM search_fts WHERE issue_slug=?", (SLUG,))
    try:
        con.execute("DELETE FROM chunk_index WHERE issue_slug=?", (SLUG,))
    except Exception:
        pass
    con.execute("DELETE FROM issues WHERE slug=?", (SLUG,))
    con.execute("DELETE FROM users WHERE feishu_open_id=? OR username=?", ("ou_agent_e2e", "agent_e2e"))
    con.commit()
    _seed_user(con)
    iid = _upsert_draft(con, marker)

    from app import db

    db.reindex_issue(con, iid, items=False, rebuild_chunks=True)
    con.commit()
    n_fts_draft = con.execute(
        "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", (SLUG,)
    ).fetchone()["c"]

    before = _agent_ask(con, marker)
    before_seen = _seen_in_answer(before, marker)
    # draft IssueRef：explicit draft → IssueRef none → 不应 grounded 到 marker
    issue_mode = (before.get("context") or {}).get("issue_ref", {}).get("mode")

    _publish(con, iid)
    n_fts_pub = con.execute(
        "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", (SLUG,)
    ).fetchone()["c"]
    after = _agent_ask(con, marker)
    after_seen = _seen_in_answer(after, marker)
    after_ev = after.get("evidence_refs") or []

    ok_before = (not before_seen) and n_fts_draft == 0 and issue_mode in (
        "none",
        "latest_published",
    )
    # explicit draft → mode none；若落到其它 published 期也不应含 marker
    ok_before = (not before_seen) and n_fts_draft == 0
    ok_after = after_seen and n_fts_pub > 0 and bool(after_ev)
    ok = ok_before and ok_after

    return {
        "ok": ok,
        "marker": marker,
        "fts_draft": n_fts_draft,
        "fts_published": n_fts_pub,
        "before": {
            "seen_marker": before_seen,
            "issue_mode": issue_mode,
            "intent": before.get("intent"),
            "text_head": (before.get("text") or "")[:160],
        },
        "after": {
            "seen_marker": after_seen,
            "evidence_n": len(after_ev),
            "evidence_sample": after_ev[:5],
            "intent": after.get("intent"),
            "text_head": (after.get("text") or "")[:160],
            "fingerprint": after.get("fingerprint"),
            "trace": after.get("trace"),
        },
        "checks": {"ok_before": ok_before, "ok_after": ok_after},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--reuse-env-db",
        action="store_true",
        help="使用当前 MESH_DB/MESH_DB_URL（会写入专用 slug 并尽量清理）",
    )
    ap.add_argument(
        "--keep-slug",
        action="store_true",
        help="结束后保留测试 slug（默认删除）",
    )
    args = ap.parse_args()

    t0 = time.time()
    if args.reuse_env_db:
        from app import db

        con = db.connect()
        try:
            result = run_once(con)
            if not args.keep_slug:
                con.execute("DELETE FROM search_fts WHERE issue_slug=?", (SLUG,))
                con.execute("DELETE FROM issues WHERE slug=?", (SLUG,))
                con.execute(
                    "DELETE FROM users WHERE feishu_open_id=?", ("ou_agent_e2e",)
                )
                con.commit()
        finally:
            con.close()
    else:
        with _temp_db():
            from app import db

            con = db.connect()
            try:
                result = run_once(con)
            finally:
                con.close()

    result["latency_ms"] = int((time.time() - t0) * 1000)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = ROOT / "eval" / "reports" / "AGENT_PUBLISH_BOUNDARY_E2E.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
