import json
import sqlite3

con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row

def digest(j):
    if not j:
        return None
    d = json.loads(j)
    rels = d.get("relations") or []
    return {
        "question": (d.get("question") or "")[:40],
        "lead": (d.get("lead") or "")[:50],
        "n_rel": len(rels),
        "rel0": (rels[0].get("title") if rels else None),
        "rel1": (rels[1].get("title") if len(rels) > 1 else None),
        "kpis": d.get("kpis"),
    }

print("=== DRAFT vs PUBLISHED per issue ===")
for row in con.execute("SELECT id, slug, period_label, status, draft_json, published_json FROM issues ORDER BY date_end DESC"):
    r = dict(row)
    d = digest(r.get("draft_json"))
    p = digest(r.get("published_json"))
    same = r.get("draft_json") == r.get("published_json")
    print(f"\n--- {r['slug']} ({r['period_label']}) status={r['status']} draft==pub={same}")
    print("  draft:", d)
    print("  pub:  ", p)

print("\n=== HOME (latest published) ===")
r = con.execute("SELECT slug, period_label, date_end FROM issues WHERE status='published' ORDER BY date_end DESC LIMIT 1").fetchone()
print(dict(r) if r else None)

con.close()
