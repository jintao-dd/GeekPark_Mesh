"""Unit tests for claim_support + ranking_quality (no DB)."""
from __future__ import annotations

import os

# 单测只验 deterministic；semantic LLM 走 Gate 回归
os.environ["MESH_CLAIM_SEMANTIC"] = "0"

from app.agent.claim_support import (
    abstain_answer_for_unsupported_claim,
    assess_claim_support,
    is_strong_deterministic_claim,
)
from app.ranking_quality import apply_ranking_v1_4


def test_speculative_certainty_insufficient():
    a = assess_claim_support(
        "面壁智能下周一定会完成融资交割吗",
        contexts=[{"title": "面壁智能", "body": "已接触芯片厂商"}],
        evidence_refs=["ev:item:4112"],
    )
    assert a["support"] == "insufficient"
    assert abstain_answer_for_unsupported_claim(a)


def test_a21_mass_prod_topic_overlap_insufficient():
    """相关端侧/座舱讨论 ≠ 已经量产上车。"""
    q = "端侧模型讨论能否证明智能座舱已经量产上车"
    assert is_strong_deterministic_claim(q)
    a = assess_claim_support(
        q,
        contexts=[
            {
                "title": "面壁智能",
                "body": "端侧模型在旗舰手机与智能座舱有合作讨论（已接触）",
            }
        ],
        evidence_refs=["ev:item:4112"],
    )
    assert a["support"] == "insufficient"
    assert a["reason"] == "strong_claim_without_direct_evidence"
    assert abstain_answer_for_unsupported_claim(a)


def test_mass_prod_direct_evidence_supported():
    a = assess_claim_support(
        "智能座舱已经量产上车",
        contexts=[{"title": "座舱", "body": "该车型智能座舱已经量产上车"}],
        evidence_refs=["ev:item:1"],
    )
    assert a["support"] == "supported"


def test_universal_denial_contradicted():
    a = assess_claim_support(
        "资料证明面壁智能从未被接触",
        contexts=[{"title": "面壁智能", "body": "已接触芯片厂商"}],
        evidence_refs=["ev:item:4112"],
    )
    assert a["support"] == "contradicted"
    assert abstain_answer_for_unsupported_claim(a)


def test_universal_denial_insufficient_without_counter():
    a = assess_claim_support(
        "资料证明面壁智能从未被接触",
        contexts=[{"title": "其它公司", "body": "讨论了行业趋势"}],
        evidence_refs=["ev:item:9"],
    )
    assert a["support"] == "insufficient"


def test_weak_mention_not_over_abstain():
    a = assess_claim_support(
        "面壁智能接触了什么",
        contexts=[{"title": "面壁智能", "body": "已接触芯片厂商"}],
        evidence_refs=["ev:item:4112"],
    )
    assert a["support"] == "supported"
    assert abstain_answer_for_unsupported_claim(a) is None


def test_ranking_v14_narrow_boost_uses_orig_i():
    hits = []
    for i in range(9):
        hits.append(
            {
                "item_id": str(4100 + i),
                "title": f"t{i}",
                "body": "普通内容" if i != 6 else "端侧模型讨论",
                "score": 10.0 - i * 0.01,
                "source": "fts",
            }
        )
    out = apply_ranking_v1_4(hits, "端侧模型与智能座舱", scope_meta={"slug": "2026-8-17"})
    ids = [str(h.get("item_id")) for h in out]
    assert "4106" in ids[:5]
