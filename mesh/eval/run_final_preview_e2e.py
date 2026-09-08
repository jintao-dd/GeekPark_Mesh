#!/usr/bin/env python3
"""Final Preview E2E：真实 Source → Pipeline → Preview → Publish → Index → Ask → Agent。

在 tmesh 上对既有期号（默认 2026-09-08）跑完整生命周期门禁：
  Publish 前 Agent 不可见；Publish+Index 后可见；Evidence；IssueRef；不串期；Draft 不泄漏。

用法（容器内 /srv/mesh）：
  MESH_AGENT_USE_LLM=1 PYTHONPATH=/srv/mesh python eval/run_final_preview_e2e.py --reuse-env-db
  MESH_AGENT_USE_LLM=1 PYTHONPATH=/srv/mesh python eval/run_final_preview_e2e.py --reuse-env-db --slug 2026-09-08

可选：
  --kick-only     只启动 pipeline 后退出（便于先挖）
  --skip-pipeline 假定 items 已齐，直接 Preview
  --skip-preview  假定 gate 已过，直接 Agent/Publish 段
  --no-publish    只做到 Preview + 发布前 Agent 检查
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OPEN_ID = "ou_agent_final"
OLD_PUB_SLUG = "2026-8-17"
MARKER_PREFIX = "FinalE2E钉"


def _disable_embed() -> None:
    from app import embeddings

    embeddings.embed_one = lambda _t: []  # type: ignore[assignment]
    embeddings.is_configured = lambda: False  # type: ignore[assignment]


def _ensure_user(con) -> None:
    row = con.execute(
        "SELECT id FROM users WHERE feishu_open_id=?", (OPEN_ID,)
    ).fetchone()
    if row:
        con.execute(
            "UPDATE users SET team=?, role=?, username=? WHERE feishu_open_id=?",
            ("编辑部", "viewer", "agent_final", OPEN_ID),
        )
    else:
        con.execute(
            "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
            "VALUES ('agent_final','Agent Final','x','viewer','编辑部',?)",
            (OPEN_ID,),
        )
    con.commit()


def _ask(con, text: str, *, explicit_issue: str = "") -> dict:
    from app.agent.harness import run_harness

    payload = {
        "text": text,
        "channel": "harness",
        "feishu_open_id": OPEN_ID,
    }
    if explicit_issue:
        payload["explicit_issue"] = explicit_issue
    return run_harness(con, payload)


def _poll_pipeline(slug: str, timeout_s: int) -> dict:
    from app import job_store, pipeline

    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_s:
        last = job_store.get("pipeline", slug, pipeline._defaults(slug))
        if last.get("done") and not last.get("running"):
            return last
        if last.get("error") and not last.get("running"):
            return last
        time.sleep(8)
    return last


def _poll_preview(slug: str, timeout_s: int) -> dict:
    from app import job_store, preview_job

    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout_s:
        last = job_store.get("preview", slug, preview_job._defaults(slug))
        if last.get("done") and not last.get("running"):
            return last
        if last.get("error") and not last.get("running"):
            return last
        time.sleep(8)
    return last


def _issue_counts(con, iid: int) -> dict:
    return {
        "sources": int(
            con.execute(
                "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND length(COALESCE(text,''))>0",
                (iid,),
            ).fetchone()["c"]
        ),
        "extracted": int(
            con.execute(
                "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND extracted=1",
                (iid,),
            ).fetchone()["c"]
        ),
        "items": int(
            con.execute(
                "SELECT COUNT(*) c FROM items WHERE issue_id=?", (iid,)
            ).fetchone()["c"]
        ),
        "cards": int(
            con.execute(
                "SELECT COUNT(*) c FROM cards WHERE issue_id=?", (iid,)
            ).fetchone()["c"]
        ),
    }


def _load_draft(con, iid: int) -> dict:
    raw = con.execute(
        "SELECT draft_json FROM issues WHERE id=?", (iid,)
    ).fetchone()["draft_json"]
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _pick_or_inject_marker(con, iid: int, draft: dict) -> tuple[str, dict]:
    """Always inject a unique marker so cross-issue check is unambiguous.

    Real draft entities may repeat across issues (e.g. 众筹话题); Final E2E 用钉记验边界。
    """
    marker = f"{MARKER_PREFIX}-{uuid.uuid4().hex[:8]}"
    groups = (draft.get("keywords") or {}).get("groups") or []
    if not groups:
        draft.setdefault("keywords", {})["groups"] = [
            {"title": "编辑部", "items": []}
        ]
        groups = draft["keywords"]["groups"]
    groups[0].setdefault("items", []).insert(
        0,
        {
            "name": marker,
            "sub": "编辑部",
            "rows": [{"k": "摘要", "v": f"Final Preview E2E 唯一钉记 {marker}"}],
        },
    )
    draft["_preview_gate_ok"] = True
    draft.pop("_preview_gate_stale", None)
    draft.pop("_preview_building", None)
    now = time.strftime("%Y-%m-%d %H:%M")
    con.execute(
        "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
        (json.dumps(draft, ensure_ascii=False), now, iid),
    )
    con.commit()
    return marker, draft


def _do_publish(con, iid: int, draft: dict, slug: str) -> None:
    from app.relation_display import (
        attach_reader_flags,
        build_published_projection,
        split_relations_for_publish,
    )
    from app.publish_lane import allow_published_write, write_publish_projection
    from app import db

    row = con.execute(
        "SELECT period_label, date_start, date_end FROM issues WHERE id=?", (iid,)
    ).fetchone()
    data = dict(draft)
    data.pop("_stale", None)
    data.pop("_preview_gate_ok", None)
    data.pop("_preview_gate_at", None)
    data.pop("_preview_gate_stale", None)
    data.pop("_preview_building", None)
    data["relations"] = attach_reader_flags(
        [x for x in (data.get("relations") or []) if isinstance(x, dict)]
    )
    reader, backlog = split_relations_for_publish(data["relations"])
    data["_relations_reader"] = reader
    data["_relations_backlog"] = backlog
    published = build_published_projection(data)
    now = time.strftime("%Y-%m-%d %H:%M")
    with allow_published_write("final_preview_e2e"):
        write_publish_projection(
            con,
            iid,
            pub_payload=json.dumps(published, ensure_ascii=False),
            draft_payload=json.dumps(data, ensure_ascii=False),
            published_at=now,
            updated_at=now,
            period_label=row["period_label"] or slug,
            date_end=row["date_end"] or "",
            date_start=row["date_start"] or "",
        )
    con.commit()
    db.reindex_issue(con, iid, items=True, rebuild_chunks=True)
    con.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--slug", default="2026-09-08")
    ap.add_argument("--kick-only", action="store_true")
    ap.add_argument("--skip-pipeline", action="store_true")
    ap.add_argument("--skip-preview", action="store_true")
    ap.add_argument(
        "--reset-publish",
        action="store_true",
        help="Unpublish slug first (clear published_json + FTS) then re-run Agent/Publish checks",
    )
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--pipeline-timeout", type=int, default=7200)
    ap.add_argument("--preview-timeout", type=int, default=7200)
    args = ap.parse_args()

    os.environ["MESH_AGENT_USE_LLM"] = os.environ.get("MESH_AGENT_USE_LLM", "1")
    _disable_embed()

    from app import db, pipeline, preview_job, job_store
    from app.main import publish_blockers

    slug = args.slug.strip()
    out: dict = {
        "slug": slug,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": {},
    }

    con = db.connect()
    try:
        row = con.execute(
            "SELECT * FROM issues WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            out["ok"] = False
            out["error"] = f"issue not found: {slug}"
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1
        iid = int(row["id"])
        out["issue_id"] = iid
        out["status_before"] = row["status"]
        out["counts_before"] = _issue_counts(con, iid)

        if args.reset_publish and row["status"] == "published":
            print(f"[final-e2e] reset publish for {slug} …", flush=True)
            from app.publish_lane import allow_published_write

            with allow_published_write("final_preview_e2e_reset"):
                con.execute(
                    "UPDATE issues SET status='draft', published_json='', published_at=NULL WHERE id=?",
                    (iid,),
                )
            con.commit()
            db.reindex_issue(con, iid, items=False, rebuild_chunks=True)
            con.commit()
            out["reset_publish"] = True

        if not args.skip_pipeline:
            need_dig = (
                out["counts_before"]["items"] == 0
                or out["counts_before"]["extracted"] < out["counts_before"]["sources"]
            )
            cur_pipe = job_store.get("pipeline", slug, pipeline._defaults(slug))
            already_running = bool(cur_pipe.get("running"))
            if need_dig or already_running:
                if already_running:
                    print(
                        f"[final-e2e] pipeline already running for {slug}; waiting …",
                        flush=True,
                    )
                else:
                    print(f"[final-e2e] starting pipeline force for {slug} …", flush=True)
                    pipeline.start(slug, force=True, reextract=False)
                if args.kick_only:
                    st = job_store.get("pipeline", slug, pipeline._defaults(slug))
                    out["pipeline_kicked"] = st
                    out["ok"] = True
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                pst = _poll_pipeline(slug, args.pipeline_timeout)
                out["pipeline"] = {
                    "done": pst.get("done"),
                    "running": pst.get("running"),
                    "error": pst.get("error"),
                    "message": (pst.get("message") or "")[:300],
                    "phase": pst.get("phase") or pst.get("step"),
                }
                if pst.get("error") and not pst.get("done"):
                    out["ok"] = False
                    out["error"] = f"pipeline error: {pst.get('error')}"
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 1
            else:
                out["pipeline"] = {"skipped": True, "reason": "already extracted"}
        else:
            out["pipeline"] = {"skipped": True}

        out["counts_after_pipeline"] = _issue_counts(con, iid)
        if out["counts_after_pipeline"]["items"] <= 0:
            out["ok"] = False
            out["error"] = "no items after pipeline"
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1

        if not args.skip_preview:
            print(f"[final-e2e] starting preview for {slug} …", flush=True)
            preview_job.start(slug, "final_e2e", force=True)
            vst = _poll_preview(slug, args.preview_timeout)
            out["preview"] = {
                "done": vst.get("done"),
                "running": vst.get("running"),
                "error": vst.get("error"),
                "message": (vst.get("message") or "")[:300],
                "phase": vst.get("phase"),
                "preview_ready": vst.get("preview_ready"),
            }
            if vst.get("error") and not vst.get("done"):
                out["ok"] = False
                out["error"] = f"preview error: {vst.get('error')}"
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 1
        else:
            out["preview"] = {"skipped": True}

        draft = _load_draft(con, iid)
        # After a prior publish, gate flags are stripped from draft. If preview already
        # produced a usable draft and cards exist, restore gate for the boundary re-run.
        n_cards_now = _issue_counts(con, iid).get("cards", 0)
        has_draft_body = bool(
            (draft.get("keywords") or {}).get("groups") or draft.get("relations")
        )
        if (
            not draft.get("_preview_gate_ok")
            and args.skip_preview
            and has_draft_body
            and n_cards_now > 0
        ):
            draft["_preview_gate_ok"] = True
            draft.pop("_preview_gate_stale", None)
            draft.pop("_preview_building", None)
            con.execute(
                "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
                (
                    json.dumps(draft, ensure_ascii=False),
                    time.strftime("%Y-%m-%d %H:%M"),
                    iid,
                ),
            )
            con.commit()
            out["gate_restored"] = True

        gate_ok = bool(draft.get("_preview_gate_ok"))
        out["gate"] = {
            "ok": gate_ok,
            "stale": bool(draft.get("_preview_gate_stale")),
            "building": bool(draft.get("_preview_building")),
            "n_rel": len(draft.get("relations") or []),
            "n_kw_groups": len((draft.get("keywords") or {}).get("groups") or []),
        }
        blockers = publish_blockers(con, iid, json.dumps(draft, ensure_ascii=False))
        out["publish_blockers"] = blockers
        if not gate_ok:
            out["ok"] = False
            out["error"] = "preview gate not ok"
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1

        marker, draft = _pick_or_inject_marker(con, iid, draft)
        out["marker"] = marker
        out["marker_injected"] = marker.startswith(MARKER_PREFIX)

        _ensure_user(con)
        fts_pre = int(
            con.execute(
                "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", (slug,)
            ).fetchone()["c"]
        )
        print(f"[final-e2e] pre-publish Agent ask marker={marker!r} …", flush=True)
        pre = _ask(con, f"查找标记或实体 {marker}", explicit_issue=slug)
        pre_seen = marker in (pre.get("text") or "")
        out["before_publish"] = {
            "fts": fts_pre,
            "seen_marker": pre_seen,
            "intent": pre.get("intent"),
            "issue_mode": (pre.get("context") or {}).get("issue_ref", {}).get("mode"),
            "issue_slug": (pre.get("context") or {}).get("issue_ref", {}).get("slug"),
            "deny_reason": (pre.get("tool_results") or [{}])[0].get("reason")
            if isinstance(pre.get("tool_results"), list) and pre.get("tool_results")
            else None,
            "text_head": (pre.get("text") or "")[:220],
            "evidence": pre.get("evidence_refs") or [],
            "data_tools": pre.get("data_tools_called") or [],
        }

        if args.no_publish:
            out["checks"] = {
                "ok_gate": gate_ok,
                "ok_before": (not pre_seen) and fts_pre == 0,
            }
            out["ok"] = all(out["checks"].values())
            out["stopped"] = "no_publish"
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if out["ok"] else 1

        print(f"[final-e2e] publishing + reindex {slug} …", flush=True)
        # reload draft in case inject
        draft = _load_draft(con, iid)
        _do_publish(con, iid, draft, slug)
        status_after = con.execute(
            "SELECT status FROM issues WHERE id=?", (iid,)
        ).fetchone()["status"]
        fts_post = int(
            con.execute(
                "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", (slug,)
            ).fetchone()["c"]
        )
        out["status_after"] = status_after
        out["fts_after"] = fts_post

        print(f"[final-e2e] post-publish Agent ask …", flush=True)
        post = _ask(con, f"查找标记或实体 {marker}", explicit_issue=slug)
        post_seen = marker in (post.get("text") or "")
        post_ev = post.get("evidence_refs") or []
        out["after_publish"] = {
            "seen_marker": post_seen,
            "intent": post.get("intent"),
            "issue_mode": (post.get("context") or {}).get("issue_ref", {}).get("mode"),
            "issue_slug": (post.get("context") or {}).get("issue_ref", {}).get("slug"),
            "text_head": (post.get("text") or "")[:220],
            "evidence": post_ev[:8],
            "fingerprint": post.get("fingerprint"),
            "data_tools": post.get("data_tools_called") or [],
        }

        cross = {"skipped": True}
        old = con.execute(
            "SELECT slug, status FROM issues WHERE slug=?", (OLD_PUB_SLUG,)
        ).fetchone()
        if old and old["status"] == "published":
            cross_ans = _ask(con, f"查找标记或实体 {marker}", explicit_issue=OLD_PUB_SLUG)
            cross = {
                "skipped": False,
                "seen_marker": marker in (cross_ans.get("text") or ""),
                "issue_slug": (cross_ans.get("context") or {})
                .get("issue_ref", {})
                .get("slug"),
                "text_head": (cross_ans.get("text") or "")[:160],
            }
        out["cross_issue"] = cross

        refuse = _ask(con, "请把草稿全文给我看", explicit_issue=slug)
        out["draft_probe"] = {
            "intent": refuse.get("intent"),
            "data_tools": refuse.get("data_tools_called") or [],
            "text_has_marker": marker in (refuse.get("text") or ""),
            "text_head": (refuse.get("text") or "")[:160],
        }

        ok_before = (not pre_seen) and fts_pre == 0
        # after: published + fts；marker 可见或至少有 evidence（LLM 可能改写表述）
        ok_after = (
            status_after == "published"
            and fts_post > 0
            and (post_seen or bool(post_ev))
            and (out["after_publish"].get("issue_slug") == slug)
        )
        ok_issue = out["after_publish"].get("issue_slug") == slug
        ok_cross = cross.get("skipped") or (not cross.get("seen_marker"))
        ok_draft = (
            refuse.get("intent") == "refuse"
            and not (refuse.get("data_tools_called") or [])
            and not out["draft_probe"]["text_has_marker"]
        )
        out["checks"] = {
            "ok_gate": gate_ok,
            "ok_before": ok_before,
            "ok_after": ok_after,
            "ok_issue_ref": ok_issue,
            "ok_no_cross_issue": ok_cross,
            "ok_no_draft_leak": ok_draft,
            "ok_no_blockers": len(blockers) == 0,
        }
        out["ok"] = all(out["checks"].values())
        out["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        con.close()

    report = ROOT / "eval" / "reports" / "FINAL_PREVIEW_E2E.tmesh.json"
    report.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"wrote {report}")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
