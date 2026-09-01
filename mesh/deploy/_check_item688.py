#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.db as d

c = d.connect()
iid = 688
it = dict(c.execute(
    "SELECT id, issue_id, source_id, owner_team, pointer, source_label, text, blocked FROM items WHERE id=?",
    (iid,)).fetchone())
src = dict(c.execute("SELECT id, title, substr(text,1,800) AS text_head FROM sources WHERE id=?", (it["source_id"],)).fetchone())
detail = "硅谷 BD 团队记录：面壁端侧模型已适配高通、联发科、英特尔、英伟达、AMD 及国产平台，尺寸 0.9B 到 9B，部署 Omni 跨模态模型（受访者陈述，非核实事实）"
# check keywords from detail appear in item text
keys = ["高通", "联发科", "英特尔", "英伟达", "AMD", "0.9B", "9B", "Omni"]
print("ITEM", iid)
print("team:", it["owner_team"])
print("blocked:", it["blocked"])
print("pointer:", it["pointer"])
print("source_label:", it["source_label"])
print("source_id:", it["source_id"], "title:", src["title"])
print("\nFULL ITEM TEXT:\n", it["text"])
print("\nKEY IN ITEM:", {k: (k in it["text"]) for k in keys})
print("\nSOURCE TEXT HEAD:\n", src["text_head"][:600])
