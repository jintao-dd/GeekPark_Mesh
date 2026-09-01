from app import db
con = db.connect()
for sid in (23, 24):
    r = con.execute("SELECT id, title, team, stype, text FROM sources WHERE id=?", (sid,)).fetchone()
    t = r["text"] or ""
    print(f"=== source #{sid} {r['title']} len={len(t)} ===")
    for kw in ["锦涛", "Web Coding", "design studio", "李源", "千问", "智能体", "互动 Map", "bug"]:
        idx = 0
        while True:
            i = t.find(kw, idx)
            if i < 0:
                break
            print(f"\n--- '{kw}' @ {i} ---")
            print(t[max(0, i - 120) : i + 200])
            idx = i + len(kw)
con.close()
