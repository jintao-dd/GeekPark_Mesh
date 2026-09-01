#!/usr/bin/env python3
"""验证 KG v0：merge 后 relations[] 携带 evidence。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, db_conn
from app.relation_candidates import merge_relations_from_candidates, prepare_draft_bundle
from eval.run_acceptance import _seed_golden_db


def main() -> int:
    td = tempfile.mkdtemp()
    db_conn.DB_PATH = str(Path(td) / "v.db")
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = db_conn.DB_PATH
    con = db.connect()
    db.init_db(seed=True)
    stats = _seed_golden_db(con)
    issue_id = con.execute("SELECT id FROM issues WHERE slug=?", (stats["slug"],)).fetchone()["id"]
    slug = stats["slug"]
    print(f"seed items={stats['items']} fts={stats['fts']}")

    bundle = prepare_draft_bundle(con, issue_id, slug)
    cands = bundle["relation_candidates"]
    items = bundle["item_rows"]
    print(f"issue={slug} items={len(items)} candidates={len(cands)}")

    draft = merge_relations_from_candidates({"relations": []}, cands, items)
    rels = draft.get("relations") or []
    print(f"relations_after_merge={len(rels)}")

    if not rels:
        print("NO_RELATIONS: golden 语料无跨团队候选（可接受）")
        return 0

    sample = rels[0]
    ev = sample.get("evidence") or []
    report = {
        "title": sample.get("title"),
        "teams": sample.get("teams"),
        "item_ids": sample.get("item_ids"),
        "n_evidence": len(ev),
        "evidence_0": ev[0] if ev else None,
        "relation_type": sample.get("relation_type"),
        "provenance_ok": sample.get("provenance_ok"),
        "keys": sorted(sample.keys()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = bool(sample.get("item_ids")) and len(ev) > 0 and all(
        e.get("item_id") and e.get("team") and e.get("snippet") is not None for e in ev
    )
    print(f"EVIDENCE_CHECK={'PASS' if ok else 'FAIL'}")

    pub = json.loads(
        con.execute("SELECT published_json FROM issues WHERE id=?", (issue_id,)).fetchone()["published_json"]
    )
    old_rels = pub.get("relations") or []
    if old_rels:
        old = old_rels[0]
        print(f"old_published_keys={sorted(old.keys())}")
        print(f"old_has_evidence={bool(old.get('evidence'))}")

    con.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
