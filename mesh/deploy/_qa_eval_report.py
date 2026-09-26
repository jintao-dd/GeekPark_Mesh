"""基于 prod agent_qa_log 生成召回+回答质量报告。"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from collections import Counter
from app import db


def _len_or_zero(text):
    return len(text or "")


def _extract_issue_slugs(text: str) -> list[str]:
    return re.findall(r"2026-\d{2}-\d{2}|2026-\d{1,2}-\d{1,2}", text or "")


def main():
    con = db.connect()
    rows = con.execute(
        "SELECT id, created_at, question, answer_text, intent, route, tools_called, "
        "answer_status, evidence_count, evidence_refs, trace_json "
        "FROM agent_qa_log WHERE answer_status='grounded' "
        "ORDER BY id DESC LIMIT 30"
    ).fetchall()

    samples = []
    for r in rows:
        trace = json.loads(r["trace_json"] or "{}")
        timings = trace.get("timings", {})
        total_ms = sum(v for k, v in timings.items() if isinstance(v, (int, float)))
        sample = {
            "id": r["id"],
            "created_at": str(r["created_at"]),
            "question": r["question"],
            "intent": r["intent"],
            "route": r["route"],
            "tools_called": json.loads(r["tools_called"] or "[]"),
            "answer_status": r["answer_status"],
            "evidence_count": r["evidence_count"],
            "evidence_refs_count": len(json.loads(r["evidence_refs"] or "[]")),
            "answer_chars": _len_or_zero(r["answer_text"]),
            "answer_lines": len((r["answer_text"] or "").splitlines()),
            "has_detail": bool(re.search(r"\d{1,2}/\d{1,2}|next|下一步|专访|沟通|饭局|会议|待办", r["answer_text"] or "")),
            "mentions_limitation": bool(re.search(r"缺|无法|没有.*字段|没有.*标注|底库|标签", r["answer_text"] or "")),
            "issue_slugs_in_answer": _extract_issue_slugs(r["answer_text"]),
            "model": trace.get("model_used", ""),
            "latency_total_ms": total_ms,
        }
        samples.append(sample)

    # 基础统计
    total = con.execute("SELECT COUNT(*) c FROM agent_qa_log").fetchone()["c"]
    status_counts = {}
    for row in con.execute("SELECT answer_status, COUNT(*) c FROM agent_qa_log GROUP BY answer_status"):
        status_counts[row["answer_status"]] = row["c"]

    # 路由分布
    route_counts = Counter()
    intent_counts = Counter()
    model_counts = Counter()
    for s in samples:
        route_counts[s["route"]] += 1
        intent_counts[s["intent"]] += 1
        model_counts[s["model"]] += 1

    # 长答案比例
    long_answers = [s for s in samples if s["answer_chars"] >= 200]
    detailed_answers = [s for s in samples if s["has_detail"]]
    limitation_answers = [s for s in samples if s["mentions_limitation"]]

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_qa_log": total,
        "status_distribution": status_counts,
        "eval_sample_size": len(samples),
        "route_distribution": dict(route_counts),
        "intent_distribution": dict(intent_counts),
        "model_distribution": dict(model_counts),
        "answer_length": {
            "avg_chars": round(sum(s["answer_chars"] for s in samples) / max(1, len(samples)), 1),
            "max_chars": max((s["answer_chars"] for s in samples), default=0),
            "min_chars": min((s["answer_chars"] for s in samples), default=0),
            "long_answers_>=200_chars": len(long_answers),
            "long_answer_ratio": round(len(long_answers) / max(1, len(samples)), 2),
        },
        "detail_signals": {
            "with_dates_or_next_steps": len(detailed_answers),
            "with_limitation_notes": len(limitation_answers),
        },
        "latency_avg_ms": round(sum(s["latency_total_ms"] for s in samples) / max(1, len(samples)), 1),
        "samples": samples,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
