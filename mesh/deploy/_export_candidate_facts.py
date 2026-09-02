#!/usr/bin/env python3
"""导出候选的 team_facts snippets 供人工/标签分析。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import build_relation_candidates

TARGETS = [
    "前沿社 · 新加坡",
    "小红书 · HuggingFace",
    "豆包 · 字节跳动",
    "编辑部 · 香动科技",
    "思忔 · 编辑部",
    "Founder Park · AGI",
    "AGI Playground · 影石 Insta360",
    "影石 · AGI Playground",
]


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    issue_id = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()["id"]
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, text, source_label, blocked
               FROM items WHERE issue_id=? AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    con.close()
    cands = build_relation_candidates(items)
    out = []
    for t in TARGETS:
        for c in cands:
            if (c.get("title") or "").strip() == t.strip():
                out.append(
                    {
                        "title": c.get("title"),
                        "teams": c.get("teams"),
                        "sources": c.get("sources"),
                        "item_ids": c.get("item_ids"),
                        "suggested_label": c.get("suggested_label"),
                        "team_facts": c.get("team_facts"),
                    }
                )
                break
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
