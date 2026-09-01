"""Preview 后 relations 摘要（tmesh 容器内运行）。"""
import json
import sys

from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
con = db.connect()
row = con.execute("SELECT status, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
if not row:
    print("issue_not_found", slug)
    sys.exit(1)
dj = json.loads(row["draft_json"] or "{}")
rels = dj.get("relations") or []
labels: dict[str, int] = {}
template_hits = 0
for r in rels:
    lb = (r.get("label") or "(empty)").strip()
    labels[lb] = labels.get(lb, 0) + 1
    body = r.get("body") or ""
    if "本期均有与「" in body and "」相关的记录" in body:
        template_hits += 1

print("status", row["status"])
print("relations", len(rels))
print("label_counts", json.dumps(labels, ensure_ascii=False))
print("template_body_hits", template_hits)
print("---")
for r in rels:
    print("TITLE", r.get("title"))
    print(" LABEL", r.get("label"), "weak=", r.get("weak"))
    print(" BODY", (r.get("body") or "")[:160])
    print("---")
