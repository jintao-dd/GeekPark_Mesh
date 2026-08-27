import json
import sqlite3

con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row

print("=== ISSUES ===")
for row in con.execute(
    "SELECT * FROM issues ORDER BY date_end DESC"
):
    r = dict(row)
    pub = r.get("published_json")
    draft = r.get("draft_json")
    q = ""
    src = "none"
    if pub:
        try:
            d = json.loads(pub)
            q = (d.get("question") or "")[:60]
            src = "published"
        except Exception:
            q = "BAD JSON"
    elif draft:
        try:
            d = json.loads(draft)
            q = (d.get("question") or "")[:60]
            src = "draft"
        except Exception:
            q = "BAD JSON"
    print(
        f"id={r['id']} slug={r['slug']!r} period={r['period_label']!r} status={r['status']} "
        f"dates={r.get('date_start')}..{r.get('date_end')} v={r.get('version')} json={src} q={q!r}"
    )

print("\n=== MAIL LOG (recent) ===")
for m in con.execute(
    """
    SELECT m.id, m.at, m.subject, m.ok, m.to_addr, m.error, i.slug, i.period_label
    FROM mail_log m JOIN issues i ON i.id = m.issue_id
    ORDER BY m.id DESC LIMIT 15
    """
):
    print(
        f"#{m['id']} {m['at']} slug={m['slug']!r} period={m['period_label']!r} ok={m['ok']} "
        f"to={m['to_addr']!r} subj={m['subject']!r}"
    )

print("\n=== SLUG ALIAS CHECK ===")
for slug in ("2026-8-17", "2026-08-17", "2026-08-14"):
    r = con.execute("SELECT id, slug, period_label FROM issues WHERE slug=?", (slug,)).fetchone()
    print(slug, "->", dict(r) if r else None)

con.close()
