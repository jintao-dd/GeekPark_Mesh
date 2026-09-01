#!/usr/bin/env python3
"""tmesh 全链路 E2E 验收：preview → verify → owner → publish → reindex → Ask。

在 tmesh 容器内运行（MESH_ALLOW_PROD_PUBLISH=1）：
  python deploy/run_tmesh_e2e_acceptance.py --slug 2026-8-17

不写生产库；结束输出 eval/reports/TMESH_E2E_ACCEPTANCE.md
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "eval" / "reports"


def _parse_draft(raw) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _draft_stats(data: dict) -> dict:
    rels = data.get("relations") or []
    vmeta = data.get("_verify") or {}
    return {
        "n_relations": len(rels),
        "n_weak": sum(1 for r in rels if isinstance(r, dict) and r.get("weak")),
        "n_with_evidence": sum(1 for r in rels if isinstance(r, dict) and r.get("evidence")),
        "verify_trim": {
            k: vmeta.get(k)
            for k in (
                "keywords_rows_trimmed",
                "plans_rows_trimmed",
                "contacts_rows_trimmed",
                "views_trimmed",
                "lead_ok",
            )
        },
        "lead_len": len(data.get("lead") or ""),
        "n_views": len(data.get("views") or []),
        "n_contacts_blocks": len(data.get("contacts") or []),
    }


def _weak_list(data: dict) -> list[dict]:
    out = []
    for i, r in enumerate(data.get("relations") or []):
        if isinstance(r, dict) and r.get("weak"):
            out.append({"index": i, "title": r.get("title"), "has_evidence": bool(r.get("evidence"))})
    return out


def _titles_from_blockers(blockers: list[str]) -> set[str]:
    titles: set[str] = set()
    for b in blockers:
        m = re.search(r"关系「([^」]+)」", b)
        if m:
            titles.add(m.group(1))
    return titles


def _owner_drop_blocked_relations(con, issue_id: int, draft: dict, blockers: list[str]) -> dict:
    """Owner 删除仍被 verify 拦截的关系卡（unsupported / no-evidence 等）。"""
    drop = _titles_from_blockers(blockers)
    if not drop:
        return {"dropped": 0, "titles": []}
    kept = []
    removed = []
    for r in draft.get("relations") or []:
        title = r.get("title") or ""
        if title in drop:
            removed.append(title)
            continue
        kept.append(r)
    draft["relations"] = kept
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute(
        "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
        (json.dumps(draft, ensure_ascii=False), stamp, issue_id),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (issue_id, "tmesh-e2e", "drop_blocked_relations", "", f"dropped={len(removed)}"),
    )
    con.commit()
    return {"dropped": len(removed), "titles": removed}


def _resolve_weak(con, issue_id: int, draft: dict, *, confirm_indexes: list[int], delete_indexes: list[int]) -> dict:
    """复现 main.issue_weak_relations_resolve。"""
    rels = list(draft.get("relations") or [])
    confirmed = deleted = 0
    for i in sorted(set(delete_indexes), reverse=True):
        if 0 <= i < len(rels):
            rels.pop(i)
            deleted += 1
    for i in confirm_indexes:
        if 0 <= i < len(rels) and isinstance(rels[i], dict) and rels[i].get("weak"):
            rels[i] = dict(rels[i])
            rels[i]["weak"] = False
            confirmed += 1
    draft["relations"] = rels
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute(
        "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
        (json.dumps(draft, ensure_ascii=False), stamp, issue_id),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (issue_id, "tmesh-e2e", "weak_relations", "confirm+delete", f"c={confirmed} d={deleted}"),
    )
    con.commit()
    return {"confirmed": confirmed, "deleted": deleted}


def _trace_one(con, rel: dict) -> dict:
    from deploy.prod_kg_spotcheck import _load_item, _load_source

    chain = {
        "title": rel.get("title"),
        "body_head": (rel.get("body") or "")[:120],
        "detail_head": ((rel.get("details") or [""])[0] if rel.get("details") else "")[:120],
        "steps": [],
    }
    for e in (rel.get("evidence") or [])[:2]:
        iid, sid = e.get("item_id"), e.get("source_id")
        item = _load_item(con, iid) if iid else None
        src = _load_source(con, sid) if sid else None
        chain["steps"].append({
            "snippet": (e.get("snippet") or "")[:100],
            "item_id": iid,
            "source_id": sid,
            "item_pointer": (item or {}).get("pointer"),
            "item_team": (item or {}).get("owner_team"),
            "source_title": ((src or {}).get("title") or "")[:80],
            "source_text_head": ((src or {}).get("text") or "")[:120],
        })
    return chain


def _page_check(slug: str, data: dict, base_url: str) -> dict:
    import urllib.request

    url = f"{base_url.rstrip('/')}/{slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tmesh-e2e/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        code = resp.status
    except Exception as e:
        return {"url": url, "ok": False, "error": str(e)}

    checks = {}
    title = (data.get("relations") or [{}])[0].get("title") if data.get("relations") else ""
    if title:
        short = title.split("·")[0].strip()[:8]
        checks["first_relation_title_in_html"] = short in html
    lead = (data.get("lead") or "")[:40]
    if lead:
        checks["lead_fragment_in_html"] = lead[:20] in html
    return {"url": url, "http_code": code, "ok": code == 200, "checks": checks}


def _run_ask_eval() -> dict:
    import os

    cmd = [sys.executable, str(ROOT / "eval" / "run_final_eval.py"), "--corpus", "prod"]
    env = os.environ.copy()
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    out = proc.stdout + proc.stderr
    m = re.search(r"(\d+)/25 pass", out)
    passed = int(m.group(1)) if m else -1
    return {
        "exit_code": proc.returncode,
        "passed": passed,
        "elapsed_sec": int(time.time() - t0),
        "tail": out[-2000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--skip-preview", action="store_true", help="仅 merge+regen，跳过 LLM 要点卡/草稿（调试）")
    ap.add_argument("--base-url", default="")
    args = ap.parse_args()
    slug = args.slug

    from app import db, merge
    from app import main as main_mod
    from app import relation_gate
    from deploy.prod_kg_spotcheck import audit_published, confirm_weak_relations, publish_draft, regen_draft_merge

    import os

    base_url = args.base_url or os.environ.get("MESH_BASE_URL", "http://127.0.0.1:8091")
    report: dict = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "environment": "tmesh",
        "slug": slug,
        "base_url": base_url,
        "steps": {},
    }

    con = db.connect()
    try:
        row = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
        if not row:
            print("issue not found:", slug)
            return 1
        issue_id = row["id"]

        # --- unpublish if needed (tmesh only) ---
        if row["status"] == "published":
            con.execute("UPDATE issues SET status='draft' WHERE id=?", (issue_id,))
            db.reindex_issue(con, issue_id)
            con.commit()
            report["steps"]["unpublish_for_rerun"] = True
            row = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()

        before = _draft_stats(_parse_draft(row["draft_json"]) or _parse_draft(row["published_json"]))
        report["preview_before"] = before
        report["preview_before"]["n_sources"] = con.execute(
            "SELECT COUNT(*) c FROM sources WHERE issue_id=?", (issue_id,)
        ).fetchone()["c"]
        report["preview_before"]["n_items"] = con.execute(
            "SELECT COUNT(*) c FROM items WHERE issue_id=?", (issue_id,)
        ).fetchone()["c"]

        # --- pipeline merge (sources/items 已存在则只刷新 merge) ---
        merge.apply_merge(con, issue_id)
        con.commit()
        report["steps"]["pipeline_merge"] = "ok"

        # --- preview: cards + draft + merge + verify ---
        if args.skip_preview:
            regen = regen_draft_merge(con, slug)
            report["steps"]["preview"] = {"mode": "regen_only", **regen}
        else:
            from app import job_store, preview_job

            t0 = time.time()
            st0 = preview_job._new_state(slug)
            st0["_username"] = "tmesh-e2e"
            claimed = job_store.try_claim(
                preview_job.KIND, slug, preview_job._defaults(slug), st0, force=True,
            )
            token = int((claimed or {}).get("token") or 0)
            preview_job._run(slug, "tmesh-e2e", token)
            st = preview_job.get_state(slug)
            report["steps"]["preview"] = {
                "mode": "full_preview_job",
                "token": token,
                "done": st.get("done"),
                "error": st.get("error"),
                "phase": st.get("phase"),
                "elapsed_sec": int(time.time() - t0),
                "log_tail": (st.get("log") or [])[-8:],
            }
            if st.get("error") and not st.get("done"):
                print("preview failed:", st.get("error"))
                return 1

        # merge + verify（preview 末步已做；此处再跑一遍确保 evidence 落地）
        regen = regen_draft_merge(con, slug)
        report["steps"]["regen_merge_verify"] = regen

        row = con.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
        after_preview = _parse_draft(row["draft_json"])
        report["preview_after"] = _draft_stats(after_preview)
        report["weak_before_owner"] = _weak_list(after_preview)

        items = relation_gate.items_for_issue(con, issue_id)
        blockers_pre = main_mod.publish_blockers(con, issue_id, row["draft_json"] or "")
        report["publish_blockers_before_owner"] = blockers_pre

        # --- owner: reject 1 条无 evidence 的 weak；confirm 其余有 evidence 的 weak ---
        weak = _weak_list(after_preview)
        delete_idxs = [w["index"] for w in weak if not w["has_evidence"]][:1]
        owner_reject = _resolve_weak(
            con, issue_id, after_preview, confirm_indexes=[], delete_indexes=delete_idxs,
        )
        owner_confirm = confirm_weak_relations(con, slug)
        report["owner_actions"] = {
            "reject_delete_indexes": delete_idxs,
            "reject_result": owner_reject,
            "confirm_weak": owner_confirm,
        }

        row = con.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
        blockers_post = main_mod.publish_blockers(con, issue_id, row["draft_json"] or "")
        report["publish_blockers_after_owner"] = blockers_post

        if blockers_post:
            draft = _parse_draft(row["draft_json"])
            drop_result = _owner_drop_blocked_relations(con, issue_id, draft, blockers_post)
            report["owner_actions"]["drop_blocked_relations"] = drop_result
            row = con.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
            blockers_post = main_mod.publish_blockers(con, issue_id, row["draft_json"] or "")
            report["publish_blockers_after_drop"] = blockers_post

        if blockers_post:
            report["PASS"] = False
            report["fail_reason"] = "publish blockers remain"
            _write_report(report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        # --- publish + reindex ---
        pub = publish_draft(con, slug)
        report["publish"] = pub

        row = con.execute("SELECT published_json, status, published_at FROM issues WHERE slug=?", (slug,)).fetchone()
        pub_data = _parse_draft(row["published_json"])
        report["published_stats"] = _draft_stats(pub_data)
        report["published_at"] = row["published_at"]
        report["status"] = row["status"]

        audit = audit_published(con, slug, sample_n=3, seed=31)
        report["audit"] = audit

        traces = []
        for r in (pub_data.get("relations") or []):
            if r.get("evidence"):
                traces.append(_trace_one(con, r))
                if len(traces) >= 3:
                    break
        report["evidence_traces"] = traces

        report["page_check"] = _page_check(slug, pub_data, base_url)

    finally:
        con.close()

    ask = _run_ask_eval()
    report["ask_eval"] = ask
    report["PASS"] = (
        report.get("status") == "published"
        and ask.get("passed") == 25
        and report.get("audit", {}).get("snippet_match_fail", 1) == 0
    )

    path = _write_report(report)
    print(f"Report: {path}")
    print(json.dumps({k: report[k] for k in ("PASS", "published_stats", "publish_blockers_before_owner", "publish_blockers_after_owner", "ask_eval")}, ensure_ascii=False, indent=2))
    return 0 if report["PASS"] else 1


def _write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    jp = REPORT_DIR / "tmesh_e2e_report.json"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = _markdown(report)
    mp = REPORT_DIR / "TMESH_E2E_ACCEPTANCE.md"
    mp.write_text(md, encoding="utf-8")
    return mp


def _markdown(r: dict) -> str:
    lines = [
        "# tmesh E2E 验收报告",
        "",
        f"- 时间：{r.get('generated_at')}",
        f"- 环境：**tmesh** · slug `{r.get('slug')}`",
        f"- 结果：**{'PASS' if r.get('PASS') else 'FAIL'}**",
        "",
        "## preview 前后",
        "",
        f"- 前：{json.dumps(r.get('preview_before'), ensure_ascii=False)}",
        f"- 后：{json.dumps(r.get('preview_after'), ensure_ascii=False)}",
        "",
        "## verify trim",
        "",
        f"{json.dumps((r.get('preview_after') or {}).get('verify_trim'), ensure_ascii=False)}",
        "",
        "## publish blockers",
        "",
        "**Owner 前：**",
    ]
    for b in r.get("publish_blockers_before_owner") or []:
        lines.append(f"- {b}")
    lines.extend(["", "**Owner 后（confirm weak）：**"])
    for b in r.get("publish_blockers_after_owner") or []:
        lines.append(f"- {b}")
    if r.get("owner_actions", {}).get("drop_blocked_relations"):
        lines.extend(["", "**Owner 删除 unsupported 卡：**", f"- {json.dumps(r['owner_actions']['drop_blocked_relations'], ensure_ascii=False)}"])
    if r.get("publish_blockers_after_drop") is not None:
        lines.extend(["", "**删除后 blockers：**"])
        for b in r.get("publish_blockers_after_drop") or []:
            lines.append(f"- {b}")
        if not r.get("publish_blockers_after_drop"):
            lines.append("- (none)")
    lines.extend([
        "",
        "## owner 操作",
        "",
        f"```json\n{json.dumps(r.get('owner_actions'), ensure_ascii=False, indent=2)}\n```",
        "",
        "## published",
        "",
        f"- relations: {(r.get('published_stats') or {}).get('n_relations')}",
        f"- with evidence: {(r.get('published_stats') or {}).get('n_with_evidence')}",
        f"- published_at: {r.get('published_at')}",
        "",
        "## evidence 回溯抽样",
        "",
        f"```json\n{json.dumps(r.get('evidence_traces'), ensure_ascii=False, indent=2)[:8000]}\n```",
        "",
        "## 页面检查",
        "",
        f"{json.dumps(r.get('page_check'), ensure_ascii=False)}",
        "",
        "## Ask 回归",
        "",
        f"- **{r.get('ask_eval', {}).get('passed')}/25** · exit {r.get('ask_eval', {}).get('exit_code')}",
        "",
        "## 生产未改动",
        "",
        "本脚本仅连接 tmesh DB；生产 fingerprint 见同次 SSH 验收日志。",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
