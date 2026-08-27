#!/usr/bin/env python3
"""P0 index health + optional full reindex inside container."""
from __future__ import annotations
import os, sys
from pathlib import Path

# ensure /srv/mesh on path
ROOT = Path("/srv/mesh")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import db  # noqa: E402

def stats(con):
    pub = con.execute("SELECT COUNT(*) c FROM issues WHERE status='published'").fetchone()["c"]
    all_i = con.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
    fts = con.execute("SELECT COUNT(*) c FROM search_fts").fetchone()["c"]
    facts = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
    fts_slugs = con.execute("SELECT COUNT(DISTINCT issue_slug) c FROM search_fts").fetchone()["c"]
    fact_slugs = con.execute("SELECT COUNT(DISTINCT issue_slug) c FROM entity_team_facts").fetchone()["c"]
    # published without fts
    missing_fts = con.execute("""
      SELECT slug FROM issues WHERE status='published'
      AND slug NOT IN (SELECT DISTINCT issue_slug FROM search_fts)
    """).fetchall()
    orphan_fts = con.execute("""
      SELECT DISTINCT issue_slug FROM search_fts
      WHERE issue_slug NOT IN (SELECT slug FROM issues WHERE status='published')
    """).fetchall()
    return {
        "issues_all": all_i,
        "published": pub,
        "fts_rows": fts,
        "fts_slugs": fts_slugs,
        "facts": facts,
        "fact_slugs": fact_slugs,
        "missing_fts": [r["slug"] for r in missing_fts],
        "orphan_fts": [r["issue_slug"] for r in orphan_fts],
    }

def main():
    do_reindex = "--reindex" in sys.argv
    con = db.connect()
    before = stats(con)
    print("BEFORE", before)
    if do_reindex:
        print("reindex_all_search…", flush=True)
        n = db.reindex_all_search(con)
        con.commit()
        print("reindexed_issues", n)
        after = stats(con)
        print("AFTER", after)
    # intent smoke (no LLM)
    from app import qa_structured as qs, search, tokenize as tok
    samples = [
        "过去一个月里有哪些硬件公司是编辑部接触过、但 Founder Park 团队还没接触过的？",
        "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？",
        "最近哪些海外接触是国内还没有部门跟进的？",
        "关于 AI 助听器，公司内部各团队分别知道什么？",
        "有没有人接触过 OpenAI",
        "本周编辑部接触了谁",
    ]
    print("INTENT_SMOKE")
    for q in samples:
        intent = qs.parse_intent(q)
        if intent:
            mode = intent.get("type")
            extra = {k: intent.get(k) for k in ("team_a", "team_b", "topic", "date_from", "section") if intent.get(k) is not None}
        else:
            mode = "lexical"
            extra = {"date_from": search.lexical_date_from(q), "match": tok.build_match_query(q)}
        # structured count if possible
        n = None
        if intent and intent.get("type") not in (None, "error"):
            r = qs.run_structured(con, intent)
            n = r.get("total")
            mode = f"structured:{intent.get('type')}"
            extra["total"] = n
            extra["ctx"] = max(0, len(r.get("contexts") or []) - 1)
        elif not intent:
            hits = search.fts_search(con, q, limit=40, date_from=search.lexical_date_from(q))
            extra["hits"] = len(hits)
        print(f"  [{mode}] {q[:36]}… -> {extra}")
    con.close()
    print("DONE")

if __name__ == "__main__":
    main()
