#!/usr/bin/env python3
"""Block mis-attributed items and patch 面壁智能·詹杨帆 relation on an issue."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.owner_guard import filter_draft_relations


def patch_relation_text(rel: dict) -> dict | None:
    title = (rel.get("title") or "").strip()
    if "詹杨帆" not in title and "面壁" not in title:
        return rel
    details = []
    for line in rel.get("details") or []:
        if "硅谷 BD" in line and "詹杨帆" in line:
            continue
        if line.strip().startswith("硅谷 BD"):
            continue
        details.append(line)
    teams = [t for t in (rel.get("teams") or []) if "硅谷 BD" not in str(t)]
    if not details and not teams:
        return None
    rel = dict(rel)
    rel["details"] = details
    rel["teams"] = teams
    srcs = [s for s in (rel.get("sources") or []) if "硅谷 BD" not in str(s)]
    if srcs:
        rel["sources"] = srcs
    if len(teams) <= 1:
        rel["weak"] = True
        rel["body"] = "端侧模型上车与智能座舱方向，编辑部有对话记录。"
    return rel


def patch_keywords(data: dict) -> dict:
    kw = data.get("keywords") or {}
    groups = kw.get("groups") or []
    for g in groups:
        for item in g.get("items") or []:
            name = (item.get("name") or "").strip()
            if "詹杨帆" not in name and "面壁" not in name:
                continue
            rows = [r for r in (item.get("rows") or []) if "硅谷 BD" not in str(r.get("k", ""))]
            item["rows"] = rows
            teams = [t for t in (item.get("teams") or []) if "硅谷 BD" not in str(t)]
            item["teams"] = teams
    return data


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    item_ids = [686, 687]
    if len(sys.argv) > 2:
        item_ids = [int(x) for x in sys.argv[2].split(",")]

    con = db.connect()
    issue = con.execute("SELECT id, slug, draft_json, published_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not issue:
        print("issue not found")
        sys.exit(1)
    iid = issue["id"]
    for iid_item in item_ids:
        con.execute(
            "UPDATE items SET blocked=1, owner_team='编辑部' WHERE id=? AND issue_id=?",
            (iid_item, iid),
        )
        print(f"blocked item {iid_item}")

    item_rows = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, blocked
               FROM items WHERE issue_id=? AND merged_into IS NULL""",
            (iid,),
        )
    ]

    for field in ("draft_json", "published_json"):
        raw = issue[field]
        if not raw:
            continue
        data = json.loads(raw)
        rels = []
        for r in data.get("relations") or []:
            pr = patch_relation_text(r)
            if pr:
                rels.append(pr)
        data["relations"] = rels
        data = patch_keywords(data)
        data = filter_draft_relations(data, item_rows)
        con.execute(f"UPDATE issues SET {field}=? WHERE id=?", (json.dumps(data, ensure_ascii=False), iid))
        print(f"patched {field}")

    db.reindex_issue(con, iid)
    con.commit()
    print("done", slug)


if __name__ == "__main__":
    main()
