#!/usr/bin/env python3
"""P0 live LLM smoke (2 questions)."""
from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = Path("/srv/mesh")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import db, qa_structured as qs, search, llm

con = db.connect()

q1 = "关于 AI 助听器，公司内部各团队分别知道什么？"
intent = qs.parse_intent(q1)
r = qs.run_structured(con, intent)
print("Q1_MODE", "structured", "total", r.get("total"), "ctx", len(r.get("contexts") or []))
try:
    ans1 = llm.answer_question(q1, r["contexts"], mode="structured")
    print("Q1_ANS_OK", len(ans1 or []))
    print("Q1_ANS", (ans1 or "")[:800])
    has_src = "来源" in (ans1 or "")
    print("Q1_HAS_SOURCE", has_src)
except Exception as e:
    print("Q1_ERR", type(e).__name__, e)

print("---")

q2 = "有没有人接触过 OpenAI"
df = search.lexical_date_from(q2)
hits = search.fts_search(con, q2, limit=40, date_from=df)
ctx = search.ask_contexts_from_hits(hits)
print("Q2_MODE", "lexical", "hits", len(hits))
try:
    ans2 = llm.answer_question(q2, ctx, mode="lexical")
    print("Q2_ANS_OK", len(ans2 or []))
    print("Q2_ANS", (ans2 or "")[:800])
    print("Q2_HAS_SOURCE", "来源" in (ans2 or ""))
except Exception as e:
    print("Q2_ERR", type(e).__name__, e)

# draft leak check: any non-published in fts?
leak = con.execute("""
  SELECT COUNT(*) c FROM search_fts
  WHERE issue_slug NOT IN (SELECT slug FROM issues WHERE status='published')
""").fetchone()["c"]
print("FTS_NONPUBLISHED_ROWS", leak)

con.close()
print("DONE")
