from app import preview_job, job_store, db
import json

slug = "2026-8-17"
print("preview_state", preview_job.get_state(slug))
print("job_store", job_store.get("preview", slug, {}))
con = db.connect()
r = con.execute("SELECT draft_json=published_json AS synced, length(draft_json) d, length(published_json) p FROM issues WHERE slug=?", (slug,)).fetchone()
print("db", dict(r))
for col in ("draft_json", "published_json"):
    d = json.loads(con.execute(f"SELECT {col} FROM issues WHERE slug=?", (slug,)).fetchone()[0] or "{}")
    print(col, "relations", len(d.get("relations") or []), "kpi", next((x.get("n") for x in (d.get("kpis") or []) if x.get("label")=="可同步的关系"), "?"))
con.close()
