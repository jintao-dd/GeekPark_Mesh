#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.db as d

c = d.connect()
pub = json.loads(c.execute("SELECT published_json FROM issues WHERE slug='2026-8-17'").fetchone()["published_json"])

# keywords / lead 抽检
lead = pub.get("lead") or ""
kw_hits = []
for g in (pub.get("keywords") or {}).get("groups") or []:
    for it in g.get("items") or []:
        name = it.get("name") or ""
        if "詹杨帆" in name or "面壁" in name:
            rows = it.get("rows") or []
            teams = it.get("teams") or []
            kw_hits.append({"name": name, "teams": teams, "rows": rows[:3]})

# 来源统计
src_counter = {}
for r in pub.get("relations") or []:
    for s in r.get("sources") or []:
        src_counter[s] = src_counter.get(s, 0) + 1

print("LEAD has 詹杨帆:", "詹杨帆" in lead)
print("LEAD has 硅谷BD假句:", "物理世界" in lead and "詹杨帆" in lead)
print("KEYWORDS hits:", json.dumps(kw_hits, ensure_ascii=False, indent=2))
print("\nTOP published source labels (relations):")
for s, n in sorted(src_counter.items(), key=lambda x: -x[1])[:15]:
    print(f"  {n}x {s}")
