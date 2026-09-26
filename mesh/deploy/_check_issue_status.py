#!/usr/bin/env python3
from app import db
import json
con = db.connect()
r = con.execute("SELECT slug, status, published_at FROM issues WHERE slug=?", ("2026-09-01",)).fetchone()
print(json.dumps(dict(r), ensure_ascii=False, default=str))
con.close()
