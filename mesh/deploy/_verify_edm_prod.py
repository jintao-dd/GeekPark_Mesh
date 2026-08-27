#!/usr/bin/env python3
import sqlite3
from app import main
from app.edm import render_edm

con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
r, data, src = main.load_issue_for_edm(con, "2026-8-17")
h, _ = render_edm(dict(r), data or {}, "https://mesh.geekpark.ai")
print("source", src, "len", len(h))
print("data-num", "data-num" in h)
print("grid", "display:grid" in h)
print("inline_list", 'bordercolor="#E6EDE0"' in h)
print("num2", "num_2.png" in h)
idx_title = h.find("其余四节，在 Mesh 里看")
idx_num = h.find("num_2.png")
print("num_after_title", idx_num > idx_title)
