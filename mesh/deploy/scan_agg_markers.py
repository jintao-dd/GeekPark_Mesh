import re, sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
text = c.execute("SELECT text FROM sources WHERE title LIKE '%数据聚合%' ORDER BY id DESC LIMIT 1").fetchone()[0]
markers = []
for i, line in enumerate(text.splitlines()):
    s = line.strip()
    if not s:
        continue
    if re.match(r"^[\U0001F300-\U0001FAFF📆📊🏢🗂]", s):
        markers.append((i, s[:100]))
    elif re.match(r"^(TechCrunch|Wired|Ars|The Verge|MIT Technology|飞书|Notion CRM|视频号|GP |Global Partnership|.*例会|.*工作周报|.*工作进展)", s):
        if len(s) < 120:
            markers.append((i, s[:100]))
print("total lines", len(text.splitlines()), "chars", len(text))
print("--- markers", len(markers))
for i, s in markers[:80]:
    print(f"{i:5d}|{s}")
