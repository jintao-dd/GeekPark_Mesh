import json
import app.db as d

c = d.connect()
for field in ("draft_json", "published_json"):
    row = c.execute(f"SELECT {field} FROM issues WHERE slug='2026-8-17'").fetchone()
    data = json.loads(row[field] or "{}")
    kpis = data.get("kpis") or []
    rel = len(data.get("relations") or [])
    kpi_rel = next((k.get("n") for k in kpis if k.get("label") == "可同步的关系"), "?")
    print(field, "relations", rel, "kpi_rel", kpi_rel)
    for k in kpis:
        print(" ", k.get("n"), k.get("label"))
