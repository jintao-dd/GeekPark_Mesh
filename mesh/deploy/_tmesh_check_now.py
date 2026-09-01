import json
import app.db as d
from app import job_store

slug = "2026-8-17"
st = job_store.get("pipeline", slug, {})
print("pipeline_state", json.dumps(st, ensure_ascii=False, indent=2))

c = d.connect()
rows = c.execute(
    """SELECT id, owner_team, owner_provenance, llm_owner_team_hint, blocked, source_label
       FROM items WHERE id IN (686,687,688,673,674,669) ORDER BY id"""
).fetchall()
print("--- key items ---")
for r in rows:
    print(dict(r))

agg = c.execute(
    """SELECT owner_provenance, COUNT(*) c FROM items
       WHERE issue_id=(SELECT id FROM issues WHERE slug=?) GROUP BY owner_provenance ORDER BY c DESC""",
    (slug,),
).fetchall()
print("--- provenance counts ---")
for r in agg:
    print(dict(r))

bad = c.execute(
    """SELECT COUNT(*) c FROM items
       WHERE issue_id=(SELECT id FROM issues WHERE slug=?)
       AND owner_team='内容中心·数据聚合'""",
    (slug,),
).fetchone()["c"]
print("placeholder_owner_count", bad)
