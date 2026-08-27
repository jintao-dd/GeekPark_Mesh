import json, sqlite3, re
con = sqlite3.connect("/srv/mesh/data/mesh.db")
r = con.execute("SELECT draft_json, published_json FROM issues WHERE slug=?", ("2026-08-14",)).fetchone()
for name, raw in [("draft", r[0]), ("published", r[1])]:
    if not raw:
        continue
    print("===", name, "len", len(raw), "hits", raw.count("否定"))
    for m in re.finditer("否定", raw):
        i = m.start()
        print(repr(raw[max(0, i - 50) : i + 50]))
