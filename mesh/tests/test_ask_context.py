"""Context Router：context_refs 继承，禁止 Answer 进成文。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_context, ask_query


def test_independent_long_question_clears_history():
    h = [
        {
            "role": "user",
            "content": "具身智能有哪些公司",
            "meta": {},
        },
        {
            "role": "assistant",
            "content": "甲乙丙都是答案正文不应被继承",
            "meta": {
                "analysis_id": "a1",
                "context_refs": {
                    "analysis_id": "a1",
                    "last_user_q": "具身智能有哪些公司",
                    "entities": ["甲", "乙"],
                    "chunk_ids": ["c1"],
                    "teams": [],
                    "issues": [],
                    "item_ids": [],
                },
            },
        },
    ]
    q = "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？"
    r = ask_context.route(q, h)
    assert r["kind"] == "independent"
    assert r["history"] == []
    assert not (r.get("context_refs") or {}).get("chunk_ids")
    assert r["search_q"] == q
    assert ask_query.retrieval_query(q, h) == q


def test_followup_inherits_refs_not_answer():
    h = [
        {"role": "user", "content": "具身智能有哪些公司", "meta": {}},
        {
            "role": "assistant",
            "content": "秘密答案XYZ不应出现在检索问句",
            "meta": {
                "analysis_id": "a1",
                "context_refs": {
                    "analysis_id": "a1",
                    "last_user_q": "具身智能有哪些公司",
                    "entities": ["优必选"],
                    "chunk_ids": ["chk-1"],
                    "teams": ["编辑部"],
                    "issues": ["2026-8-17"],
                    "item_ids": ["9"],
                },
            },
        },
    ]
    r = ask_context.route("还有哪些", h)
    assert r["kind"] == "followup"
    assert r["history"] == []  # 禁止 Answer/对话进成文
    assert r["context_refs"]["chunk_ids"] == ["chk-1"]
    assert r["parent_analysis_id"] == "a1"
    assert "具身智能" in r["search_q"]
    assert "秘密答案XYZ" not in r["search_q"]
    assert ask_query.retrieval_query("还有哪些", h).find("具身智能") >= 0


def test_followup_anaphora_with_fallback_user_q():
    h = [
        {"role": "user", "content": "编辑部接触了面壁智能吗", "meta": {}},
        {"role": "assistant", "content": "有接触，这是旧答案", "meta": {}},
    ]
    r = ask_context.route("那家最近怎么样", h)
    assert r["kind"] == "followup"
    assert r["history"] == []
    assert "面壁" in r["search_q"] or "编辑部" in r["search_q"]
    assert "旧答案" not in r["search_q"]


def test_build_refs_excludes_answer():
    refs = ask_context.build_context_refs(
        analysis_id="x1",
        q="问句",
        contexts=[
            {"期号": "2026-8-17", "归属团队": "编辑部", "标题": "面壁智能", "内容": "接触", "chunk_id": "c9"},
        ],
        sources=[{"source": "编辑部", "issue": "2026-8-17"}],
        cross={"same_entities": ["面壁智能"]},
    )
    assert refs["analysis_id"] == "x1"
    assert "面壁智能" in refs["entities"]
    assert "c9" in refs["chunk_ids"]
    assert "编辑部" in refs["teams"]


def test_short_entity_stays_independent():
    h = [
        {"role": "user", "content": "具身智能有哪些公司", "meta": {}},
        {
            "role": "assistant",
            "content": "甲公司乙公司",
            "meta": {
                "context_refs": {
                    "analysis_id": "a1",
                    "last_user_q": "具身智能有哪些公司",
                    "entities": ["优必选"],
                    "chunk_ids": [],
                    "teams": [],
                    "issues": [],
                    "item_ids": [],
                },
            },
        },
    ]
    r = ask_context.route("面壁智能", h)
    assert r["kind"] == "independent"
    assert r["search_q"] == "面壁智能"


def test_needs_cross():
    one = [{"group_id": "g1", "source": "编辑部", "entities": ["甲"]}]
    two = one + [{"group_id": "g2", "source": "商业化团队", "entities": ["甲"]}]
    assert ask_context.needs_cross("编辑部本周接触了谁", one) is False
    assert ask_context.needs_cross("两边同时跟进了谁", two) is True
    assert ask_context.needs_cross("随便问问", two) is False
    assert ask_context.needs_cross("交集", two, mode="structured") is True
