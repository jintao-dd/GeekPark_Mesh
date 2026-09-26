#!/usr/bin/env python3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import db
con = db.connect()
rows = con.execute("SELECT slug, id, period_label, updated_at, status FROM issues ORDER BY id DESC LIMIT 15").fetchall()
for r in rows:
    print(r["slug"], "|", r["period_label"], "|", r["status"], "|", r["updated_at"])
con.close()
