"""Render EDM for an issue slug and print structural checks."""
import json
import sqlite3
import sys

from app import edm

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
if not r:
    print("NO_ISSUE", slug)
    sys.exit(1)
issue = dict(r)
pub = con.execute(
    "SELECT data FROM issue_published WHERE issue_id=?", (issue["id"],)
).fetchone()
draft = con.execute(
    "SELECT data FROM issue_drafts WHERE issue_id=? ORDER BY id DESC LIMIT 1",
    (issue["id"],),
).fetchone()
row = pub or draft
data = json.loads(row["data"]) if row else {}
h, _ = edm.render_edm(issue, data, "https://mesh.geekpark.ai", "")
checks = {
    "logo": "mesh_logo.png" in h,
    "neon_kpi": "C6FF3F" in h,
    "num_1": "num_1.png" in h,
    "card_border": "E6EDE0" in h,
    "sec1_solid_dashed": "实线标签" in h and "虚线标签" in h,
    "src_bg": "F4FAEC" in h,
    "deep_footer": "仅限极客公园内部使用" in h,
    "hero_bigdate": "CBD0D6" in h,
    "relations": len(data.get("relations", [])),
}
print("ISSUE", issue["slug"], issue["period_label"], issue.get("version"))
for k, v in checks.items():
    print(k, v)
with open("/tmp/edm_preview.html", "w", encoding="utf-8") as f:
    f.write(h)
print("WROTE", "/tmp/edm_preview.html", "bytes", len(h))
