from app import db
con = db.connect()
t = con.execute("SELECT text FROM sources WHERE id=23").fetchone()["text"]
needle = "千问公关"
idx = 0
n = 0
while n < 5:
    i = t.find(needle, idx)
    if i < 0:
        break
    print(f"=== hit {n+1} @ {i} ===")
    print(t[max(0, i-300):i+800])
    print()
    idx = i + len(needle)
    n += 1
con.close()
