"""Unit tests for claim_support + ranking_quality (no DB)."""
from __future__ import annotations

from app.agent.claim_support import (
    abstain_answer_for_unsupported_claim,
    assess_claim_support,
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


def test_universal_denial_contradicted():
    a = assess_claim_support(
        "资料证明面壁智能从未被接触",
        contexts=[{"title": "面壁智能", "body": "已接触芯片厂商"}],
        evidence_refs=["ev:item:4112"],
    )
    assert a["support"] == "contradicted"
    assert abstain_answer_for_unsupported_claim(a)


def test_temporal_not_speculative():
    a = assess_claim_support(
        "面壁智能接触了什么",
        contexts=[{"title": "面壁智能", "body": "已接触芯片厂商"}],
        evidence_refs=["ev:item:4112"],
    )
    assert a["support"] == "supported"
    assert abstain_answer_for_unsupported_claim(a) is None


def test_ranking_v14_narrow_boost_uses_orig_i():
    # FTS order: 6 fillers then target at orig 6 (7th)
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
    # target should move up vs pure FTS position 7
    ids = [str(h.get("item_id")) for h in out]
    assert "4106" in ids[:5]
