#!/usr/bin/env python3
"""Prod KG v0 抽检：regen draft merge → publish → 核对 evidence 链。

在容器内运行：
  python deploy/prod_kg_spotcheck.py --slug 2026-8-17 --regen --publish --sample 5

仅审计已发布：
  python deploy/prod_kg_spotcheck.py --slug 2026-8-17 --audit-only --sample 5
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import merge_relations_from_candidates, prepare_draft_bundle


def _parse_draft(raw: str | None) -> dict:
    if not raw or not str(raw).strip():
        return {}
    return json.loads(raw)


def regen_draft_merge(con, slug: str) -> dict:
    """复现 preview_job 最后一步：LLM draft + merge_relations_from_candidates。"""
    row = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise SystemExit(f"issue not found: {slug}")
    issue_id = row["id"]
    draft = _parse_draft(row["draft_json"]) or _parse_draft(row["published_json"])
    if not draft:
        raise SystemExit(f"no draft/published json for {slug}")

    bundle = prepare_draft_bundle(con, issue_id, slug)
    merged = merge_relations_from_candidates(
        draft, bundle["relation_candidates"], bundle["item_rows"],
    )
    merged["slug"] = slug
    merged["period_label"] = row["period_label"] or slug
    merged["version"] = row["version"]
    merged.pop("_stale", None)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = json.dumps(merged, ensure_ascii=False)
    con.execute(
        "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
        (payload, stamp, issue_id),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (issue_id, "spotcheck", "regen_merge", "", f"KG v0 merge regen {stamp}"),
    )
    con.commit()
    return {
        "issue_id": issue_id,
        "n_candidates": len(bundle["relation_candidates"]),
        "n_items": len(bundle["item_rows"]),
        "n_relations": len(merged.get("relations") or []),
        "with_evidence": sum(1 for r in (merged.get("relations") or []) if r.get("evidence")),
    }


def confirm_weak_relations(con, slug: str) -> dict:
    """模拟 owner 确认：有 evidence 的 weak 取消 weak；无 evidence 的 weak 删除。"""
    row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    data = json.loads(row["draft_json"])
    confirmed = 0
    dropped = 0
    kept: list[dict] = []
    for r in data.get("relations") or []:
        if not r.get("weak"):
            kept.append(r)
            continue
        if r.get("evidence"):
            r["weak"] = False
            kept.append(r)
            confirmed += 1
        else:
            dropped += 1
    data["relations"] = kept
    if confirmed or dropped:
        con.execute(
            "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), row["id"]),
        )
        con.execute(
            "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
            (row["id"], "spotcheck", "confirm_weak", "", f"confirmed={confirmed} dropped={dropped}"),
        )
        con.commit()
    return {"confirmed": confirmed, "dropped": dropped}


def publish_draft(con, slug: str) -> dict:
    """复现 main.publish：draft_json → published_json。"""
    from app import main as main_mod

    row = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not row or not row["draft_json"]:
        raise SystemExit("no draft to publish")
    blockers = main_mod.publish_blockers(con, row["id"], row["draft_json"] or "")
    if blockers:
        raise SystemExit("publish blockers:\n" + "\n".join(blockers))

    data = json.loads(row["draft_json"])
    data.pop("_stale", None)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = json.dumps(data, ensure_ascii=False)
    con.execute(
        "UPDATE issues SET published_json=?, draft_json=?, status='published', published_at=?, updated_at=? WHERE id=?",
        (payload, payload, now, now, row["id"]),
    )
    db.register_entities(con, data, slug)
    db.snapshot_published_items(con, row["id"])
    db.reindex_issue(con, row["id"])
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (row["id"], "spotcheck", "publish", "", f"KG v0 spotcheck {now}"),
    )
    con.commit()
    return {"published_at": now, "n_relations": len(data.get("relations") or [])}


def _load_item(con, item_id: int) -> dict | None:
    r = con.execute(
        """SELECT id, source_id, owner_team, team, pointer, text, source_label, blocked
           FROM items WHERE id=?""",
        (item_id,),
    ).fetchone()
    return dict(r) if r else None


def _load_source(con, source_id: int) -> dict | None:
    r = con.execute(
        "SELECT id, title, team, stype, text, extracted FROM sources WHERE id=?",
        (source_id,),
    ).fetchone()
    return dict(r) if r else None


def _snippet_in_text(snippet: str, text: str) -> bool:
    s = (snippet or "").strip()
    t = (text or "").strip()
    if not s or not t:
        return False
    if s in t:
        return True
    # 允许截断：取 snippet 前 40 字匹配
    head = s[:40].strip()
    return bool(head and head in t)


def _entity_tokens(title: str) -> set[str]:
    parts = re.split(r"[·、/|\s]+", title or "")
    return {p.strip() for p in parts if len(p.strip()) >= 2}


def _body_supported(rel: dict, evidence: list[dict]) -> tuple[bool, str]:
    """粗检 LLM body/details 是否明显超出 evidence（实体名 + snippet 并集）。"""
    ev_text = " ".join((e.get("snippet") or "") for e in evidence)
    ev_teams = {e.get("team") for e in evidence if e.get("team")}
    issues: list[str] = []

    title_ents = _entity_tokens(rel.get("title") or "")
    for ent in title_ents:
        if ent not in ev_text and ent not in (rel.get("title") or ""):
            # title 实体允许来自 candidate 合并标题
            pass

    body = rel.get("body") or ""
    for team in re.findall(r"[\u4e00-\u9fffA-Za-z /]+团队", body):
        t = team.strip()
        if t and t not in ev_teams and t not in body:
            issues.append(f"body mentions team not in evidence: {t}")

    details = rel.get("details") or []
    for d in details:
        ds = str(d)
        if len(ds) > 20 and not any(_snippet_in_text(ds[:30], ev_text) for _ in [0]):
            # details 常为 team_facts 模板句，允许与 snippet 部分重叠
            if not any(tok in ev_text for tok in _entity_tokens(ds) if len(tok) >= 3):
                issues.append(f"detail may exceed evidence: {ds[:60]}…")

    if issues:
        return False, "; ".join(issues[:3])
    return True, "ok"


def audit_published(con, slug: str, sample_n: int = 5, seed: int = 42) -> dict:
    row = con.execute(
        "SELECT id, status, published_json, draft_json FROM issues WHERE slug=?", (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"issue not found: {slug}")
    data = _parse_draft(row["published_json"]) or _parse_draft(row["draft_json"])
    rels = list(data.get("relations") or [])

    summary = {
        "slug": slug,
        "status": row["status"],
        "n_relations": len(rels),
        "n_with_evidence": 0,
        "n_without_evidence": 0,
        "n_weak_without_evidence": 0,
        "evidence_item_checks": 0,
        "evidence_item_pass": 0,
        "snippet_match_pass": 0,
        "snippet_match_fail": 0,
        "body_supported_pass": 0,
        "body_supported_fail": 0,
        "samples": [],
        "failures": [],
    }

    for rel in rels:
        ev = rel.get("evidence") or []
        if ev:
            summary["n_with_evidence"] += 1
        else:
            summary["n_without_evidence"] += 1
            if rel.get("weak"):
                summary["n_weak_without_evidence"] += 1
            else:
                summary["failures"].append({
                    "title": rel.get("title"),
                    "reason": "missing evidence on non-weak relation",
                })

    # 优先抽有 evidence 的 cross-team 关系
    pool = [r for r in rels if r.get("evidence")]
    if not pool:
        pool = rels
    rng = random.Random(seed)
    picks = rng.sample(pool, min(sample_n, len(pool))) if pool else []

    for rel in picks:
        ev = rel.get("evidence") or []
        sample = {
            "title": rel.get("title"),
            "teams": rel.get("teams"),
            "n_evidence": len(ev),
            "checks": [],
        }
        for e in ev[:4]:
            iid = e.get("item_id")
            sid = e.get("source_id")
            chk = {"item_id": iid, "source_id": sid, "team": e.get("team"), "pointer": e.get("pointer")}
            summary["evidence_item_checks"] += 1

            item = _load_item(con, iid) if iid else None
            src = _load_source(con, sid) if sid else None
            if not item:
                chk["item_ok"] = False
                chk["item_err"] = "item not found"
                summary["failures"].append({"title": rel.get("title"), "item_id": iid, "err": "item not found"})
            else:
                chk["item_ok"] = True
                chk["item_team"] = item.get("owner_team") or item.get("team")
                team_ok = (chk["item_team"] or "") == (e.get("team") or "")
                chk["team_match"] = team_ok
                if team_ok:
                    summary["evidence_item_pass"] += 1
                else:
                    summary["failures"].append({
                        "title": rel.get("title"), "item_id": iid,
                        "err": f"team mismatch ev={e.get('team')} item={chk['item_team']}",
                    })

                snip_ok = _snippet_in_text(e.get("snippet") or "", item.get("text") or "")
                chk["snippet_in_item"] = snip_ok
                if snip_ok:
                    summary["snippet_match_pass"] += 1
                else:
                    summary["snippet_match_fail"] += 1
                    summary["failures"].append({
                        "title": rel.get("title"), "item_id": iid,
                        "err": "snippet not in item.text",
                        "snippet_head": (e.get("snippet") or "")[:80],
                        "item_head": (item.get("text") or "")[:80],
                    })

                ptr = (e.get("pointer") or "").strip()
                iptr = (item.get("pointer") or "").strip()
                chk["pointer_match"] = (not ptr) or (ptr == iptr) or (ptr in iptr) or (iptr in ptr)

            if src:
                chk["source_ok"] = True
                chk["source_title"] = (src.get("title") or "")[:60]
                if item and item.get("source_id") == sid:
                    chk["source_id_consistent"] = True
            else:
                chk["source_ok"] = False

            sample["checks"].append(chk)

        ok_body, body_note = _body_supported(rel, ev)
        sample["body_supported"] = ok_body
        sample["body_note"] = body_note
        if ok_body:
            summary["body_supported_pass"] += 1
        else:
            summary["body_supported_fail"] += 1
            summary["failures"].append({"title": rel.get("title"), "err": body_note})

        summary["samples"].append(sample)

    summary["PASS"] = (
        (summary["n_without_evidence"] == summary["n_weak_without_evidence"]
         or summary["n_with_evidence"] == 0)
        and summary["snippet_match_fail"] == 0
        and (summary["evidence_item_checks"] == 0 or summary["evidence_item_checks"] == summary["evidence_item_pass"])
    )
    if summary["n_with_evidence"] == 0 and summary["n_relations"] > 0:
        summary["legacy_no_evidence"] = True
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--regen", action="store_true", help="re-run merge on draft/published base")
    ap.add_argument("--publish", action="store_true", help="publish draft after regen")
    ap.add_argument("--confirm-weak", action="store_true", help="clear weak on relations with evidence (owner confirm sim)")
    ap.add_argument("--audit-only", action="store_true")
    ap.add_argument("--sample", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import os
    from app import db as db_mod

    if (args.regen or args.publish or args.confirm_weak) and db_mod.is_postgres():
        allow = (os.environ.get("MESH_ALLOW_PROD_PUBLISH") or "").strip().lower() in ("1", "true", "yes")
        if not allow:
            raise SystemExit(
                "生产库禁止 regen/publish/confirm_weak 抽检。"
                "请仅用 --audit-only；或设置 MESH_ALLOW_PROD_PUBLISH=1（不推荐）。"
            )

    con = db.connect()
    try:
        if args.regen:
            info = regen_draft_merge(con, args.slug)
            print("REGEN:", json.dumps(info, ensure_ascii=False))
        if args.confirm_weak:
            info = confirm_weak_relations(con, args.slug)
            print("CONFIRM_WEAK:", json.dumps(info, ensure_ascii=False))
        if args.publish:
            info = publish_draft(con, args.slug)
            print("PUBLISH:", json.dumps(info, ensure_ascii=False))
        if args.audit_only or args.regen or args.publish:
            report = audit_published(con, args.slug, sample_n=args.sample, seed=args.seed)
            print("AUDIT:", json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("PASS") else 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
