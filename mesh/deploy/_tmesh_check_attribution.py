"""Check attribution after re-extract on tmesh."""
import json
import time
import app.db as db
from app import job_store

slug = "2026-8-17"

for _ in range(120):
    st = job_store.get("pipeline", slug, {})
    if st.get("done"):
        print("pipeline_done", st.get("message"))
        break
    if st.get("error"):
        print("pipeline_error", st.get("error"))
        break
    print("pipeline_running", st.get("phase"), st.get("message"))
    time.sleep(15)
else:
    print("pipeline_timeout")

con = db.connect()
rows = con.execute(
    """SELECT id, owner_team, owner_provenance, llm_owner_team_hint, blocked, source_label
       FROM items WHERE id IN (686,687,688,673,674) ORDER BY id"""
).fetchall()
print("--- key items ---")
for r in rows:
    print(dict(r))

agg = con.execute(
    """SELECT owner_provenance, COUNT(*) c FROM items
       WHERE issue_id=(SELECT id FROM issues WHERE slug=?) GROUP BY owner_provenance""",
    (slug,),
).fetchall()
print("--- provenance counts ---")
for r in agg:
    print(dict(r))

llm_as_owner = con.execute(
    """SELECT COUNT(*) c FROM items
       WHERE issue_id=(SELECT id FROM issues WHERE slug=?)
       AND owner_provenance='llm_hint' AND owner_team IS NOT NULL AND owner_team<>''""",
    (slug,),
).fetchone()["c"]
print("llm_hint_written_as_owner", llm_as_owner)
