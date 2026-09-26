"""召回质量抽查：对 agent_qa_log 里的 grounded 问题，检查 evidence_refs 覆盖情况。"""
from __future__ import annotations
import json
import re
from collections import Counter
from app import db, search


def _issue_slug_from_evidence(ref: str) -> str:
    # ev:ctx:2026-09-15:1  ev:item:2026-8-17:21  crm:take:Yu Su
    if ref.startswith("ev:ctx:"):
        return ref.split(":")[2]
    if ref.startswith("ev:item:"):
        return ref.split(":")[2]
    return ""


def main():
    con = db.connect()
    rows = con.execute(
        "SELECT id, question, answer_text, tools_called, evidence_refs, trace_json "
        "FROM agent_qa_log WHERE answer_status='grounded' ORDER BY id DESC LIMIT 30"
    ).fetchall()

    findings = []
    for r in rows:
        refs = json.loads(r["evidence_refs"] or "[]")
        tools = json.loads(r["tools_called"] or "[]")
        trace = json.loads(r["trace_json"] or "{}")

        # 简单召回信号
        has_item_facts = any(ref.startswith("ev:item:") for ref in refs)
        has_search_fts = any(ref.startswith("ev:ctx:") for ref in refs)
        has_crm = any(ref.startswith("crm:") for ref in refs)
        has_feishu = "feishu.search" in tools
        has_ask = "ask.published" in tools

        # issue slug 不一致检查
        slugs = [_issue_slug_from_evidence(ref) for ref in refs]
        slug_counter = Counter(s for s in slugs if s)
        mixed_slug_formats = any(
            re.match(r"^2026-\d-\d+", s) for s in slug_counter
        )

        findings.append({
            "id": r["id"],
            "question": r["question"],
            "answer_excerpt": (r["answer_text"] or "")[:120],
            "evidence_count": len(refs),
            "has_item_facts": has_item_facts,
            "has_search_fts": has_search_fts,
            "has_crm": has_crm,
            "has_feishu": has_feishu,
            "has_ask": has_ask,
            "slug_distribution": dict(slug_counter.most_common(5)),
            "mixed_slug_formats": mixed_slug_formats,
            "model": trace.get("model_used", ""),
        })

    summary = {
        "total_evaluated": len(findings),
        "with_item_facts": sum(1 for f in findings if f["has_item_facts"]),
        "with_search_fts": sum(1 for f in findings if f["has_search_fts"]),
        "with_crm": sum(1 for f in findings if f["has_crm"]),
        "with_mixed_slug_formats": sum(1 for f in findings if f["mixed_slug_formats"]),
        "avg_evidence_count": round(sum(f["evidence_count"] for f in findings) / max(1, len(findings)), 1),
        "findings": findings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
