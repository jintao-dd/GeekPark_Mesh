"""飞书/Agent 回答质量采集：落库、flag 派生、筛选、人工标注。

覆盖：证据与 answer_status 落库、无证据标记、状态筛选、人工标差。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, qa_log


def _reset(con):
    con.execute("DELETE FROM agent_qa_log")
    con.commit()


def _answer(**kw) -> dict:
    base = {
        "text": "面壁智能本周在编辑部出现。",
        "display_text": "面壁智能本周在编辑部出现。（来源：2026-09-15）",
        "intent": "fact_lookup",
        "tools_called": ["ask"],
        "evidence_refs": ["item:101"],
        "claim_bindings": [{"claim": "面壁智能", "status": "grounded", "evidence_refs": ["item:101"]}],
        "trace": {"conversation_route": "ask", "n_hits": 3, "timings": {"retrieve_ms": 42}},
        "refused": False,
    }
    base.update(kw)
    return base


def test_record_and_read_back():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        rid = qa_log.record_answer(
            con,
            envelope={"channel": "feishu_dm", "feishu_open_id": "ou_abc", "chat_id": "oc_x"},
            answer=_answer(),
            question="面壁智能这周有什么动静",
            latency_ms=1234,
            request_id="req1",
        )
        con.commit()
        assert rid
        t = qa_log.get_turn(con, rid)
        assert t["answer_status"] == "grounded"
        assert t["evidence_count"] == 1
        assert t["evidence_refs"] == ["item:101"]
        assert t["claim_bindings"][0]["status"] == "grounded"
        assert t["channel"] == "feishu_dm"
        assert t["route"] == "ask"
        assert t["trace_json"]["n_hits"] == 3
        assert t["flag"] == ""
    finally:
        con.close()


def test_no_evidence_flagged():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        rid = qa_log.record_answer(
            con,
            envelope={"channel": "feishu_group"},
            answer=_answer(evidence_refs=[], claim_bindings=[], trace={}),
            question="随便问问",
            latency_ms=100,
        )
        con.commit()
        t = qa_log.get_turn(con, rid)
        assert t["answer_status"] == "unknown"
        assert t["flag"] == "no_evidence"
    finally:
        con.close()


def test_unsupported_and_weak_flags():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        r1 = qa_log.record_answer(
            con, envelope={"channel": "harness"},
            answer=_answer(claim_bindings=[{"claim": "x", "status": "unsupported"}]),
            question="q1",
        )
        r2 = qa_log.record_answer(
            con, envelope={"channel": "harness"},
            answer=_answer(claim_bindings=[{"claim": "x", "status": "weak"}]),
            question="q2",
        )
        con.commit()
        assert qa_log.get_turn(con, r1)["flag"] == "unsupported"
        assert qa_log.get_turn(con, r2)["flag"] == "weak"
    finally:
        con.close()


def test_list_filters_and_feedback():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        good = qa_log.record_answer(
            con, envelope={"channel": "feishu_dm"}, answer=_answer(), question="a"
        )
        bad = qa_log.record_answer(
            con, envelope={"channel": "feishu_group"},
            answer=_answer(claim_bindings=[{"claim": "x", "status": "unsupported"}]),
            question="b",
        )
        con.commit()

        page = qa_log.list_turns(con, channel="feishu_dm")
        assert page["total"] == 1 and page["turns"][0]["id"] == good

        page = qa_log.list_turns(con, answer_status="grounded")
        assert page["total"] == 1

        assert qa_log.set_feedback(con, bad, feedback="bad", note="答错了", by="tester")
        con.commit()
        page = qa_log.list_turns(con, only_bad=True)
        assert page["total"] == 1 and page["turns"][0]["id"] == bad
        assert page["turns"][0]["feedback"] == "bad"
        assert page["turns"][0]["feedback_note"] == "答错了"

        assert qa_log.set_feedback(con, bad, feedback="nonsense") is False
        con.commit()
        assert qa_log.list_turns(con, only_bad=True)["total"] == 1  # 未清除
    finally:
        con.close()


def test_record_never_raises_on_bad_input():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        # answer 为怪类型也不应抛
        assert qa_log.record_answer(con, envelope=None, answer={}, question="") is not None
        con.commit()
    finally:
        con.close()


def test_stats_shape():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        qa_log.record_answer(con, envelope={"channel": "feishu_dm"}, answer=_answer(), question="a")
        con.commit()
        s = qa_log.stats(con, days=7)
        assert s["total"] >= 1
        assert "grounded" in s["by_status"]
        assert s["by_channel"].get("feishu_dm", 0) >= 1
    finally:
        con.close()
