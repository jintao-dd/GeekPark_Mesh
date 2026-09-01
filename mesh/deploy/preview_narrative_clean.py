#!/usr/bin/env python3
"""预览 Narrative 清理（不写库、不改 owner_team）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.narrative_clean import clean_detail_line, clean_source_label, preview_issue_narrative


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    con = db.connect()
    row = con.execute("SELECT id FROM issues WHERE slug=?", (args.slug,)).fetchone()
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, owner_team, source_label, blocked FROM items
               WHERE issue_id=? AND blocked=0 AND merged_into IS NULL""",
            (row["id"],),
        )
    ]
    stats = preview_issue_narrative(items)

    # relation detail 预览
    import json as _json
    draft = _json.loads(
        con.execute("SELECT draft_json FROM issues WHERE slug=?", (args.slug,)).fetchone()["draft_json"] or "{}"
    )
    detail_samples = []
    for rel in draft.get("relations") or []:
        title = rel.get("title") or ""
        for line in rel.get("details") or []:
            # 用 evidence 里第一个 item 的 owner 作参考（仅预览）
            ev = (rel.get("evidence") or [{}])[0]
            iid = ev.get("item_id")
            owner = ev.get("team") or ""
            if iid:
                it = next((x for x in items if x["id"] == iid), None)
                if it:
                    owner = it.get("owner_team") or owner
            r = clean_detail_line(str(line), owner)
            if r.action != "keep" and len(detail_samples) < 15:
                detail_samples.append({"relation": title, "before": r.original, "after": r.cleaned, "reason": r.reason})

    con.close()

    if args.json:
        print(json.dumps({"stats": stats, "detail_samples": detail_samples}, ensure_ascii=False, indent=2))
        return

    print(f"=== Narrative 清理预览 · {args.slug} ===")
    print("item source_label:", json.dumps({k: stats[k] for k in ("keep", "strip_prefix", "needs_review")}, ensure_ascii=False))
    for s in stats["samples"][:15]:
        print(f"\n  #{s['item_id']} [{s['action']}] owner={s['owner_team']}")
        print(f"    - {s['before']}")
        print(f"    + {s['after']}")
    if detail_samples:
        print("\n--- detail 行预览 ---")
        for d in detail_samples[:10]:
            print(f"  [{d['relation']}]")
            print(f"    - {d['before']}")
            print(f"    + {d['after']}")


if __name__ == "__main__":
    main()
