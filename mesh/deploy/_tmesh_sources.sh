#!/bin/bash
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python <<'PY'
import app.db as d
c = d.connect()
slug = "2026-8-17"
n = c.execute(
    "SELECT COUNT(*) c FROM sources WHERE issue_id=(SELECT id FROM issues WHERE slug=?) AND length(text)>0",
    (slug,),
).fetchone()["c"]
ext = c.execute(
    "SELECT COUNT(*) c FROM sources WHERE issue_id=(SELECT id FROM issues WHERE slug=?) AND extracted=1",
    (slug,),
).fetchone()["c"]
print("sources_total", n, "extracted", ext)
for r in c.execute(
    "SELECT id, extracted, substring(title,1,50) t, team FROM sources WHERE issue_id=(SELECT id FROM issues WHERE slug=?) ORDER BY id",
    (slug,),
):
    print(dict(r))
PY
