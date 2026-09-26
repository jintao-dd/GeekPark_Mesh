"""Tool Contract / source_tier 死契约测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import tool_contract as tc


def test_tier_truth_pairing():
    assert tc.truth_for_tier(tc.SourceTier.PUBLISHED) == tc.TruthLevel.ENTERPRISE_FACT
    assert tc.truth_for_tier(tc.SourceTier.FEISHU_LIVE) == tc.TruthLevel.LIVE_CONTEXT
    assert tc.truth_for_tier(tc.SourceTier.MODEL) == tc.TruthLevel.GENERAL_KNOWLEDGE


def test_mixed_tier_rejected():
    with pytest.raises(ValueError, match="混级"):
        tc.assert_tier_truth_pair(
            tc.SourceTier.FEISHU_LIVE, tc.TruthLevel.ENTERPRISE_FACT
        )
    with pytest.raises(ValueError, match="混级"):
        tc.ToolResultEnvelope(
            ok=True,
            tool="feishu.search",
            source_tier=tc.SourceTier.FEISHU_LIVE,
            truth_level=tc.TruthLevel.ENTERPRISE_FACT,
            items=[{"title": "x"}],
        )


def test_feishu_search_contract():
    c = tc.FEISHU_SEARCH
    assert c.name == "feishu.search"
    assert c.source_tier == tc.SourceTier.FEISHU_LIVE
    assert c.side_effect == tc.SideEffect.NONE
    assert c.confirmation_required is False
    assert "resource_type" in c.input_schema["properties"]


def test_phase2_only_doc():
    assert tc.feishu_search_type_allowed("doc", phase="2") is True
    assert tc.feishu_search_type_allowed("message", phase="2") is False
    assert tc.feishu_search_type_allowed("message", phase="5") is True


def test_write_requires_confirmation():
    with pytest.raises(ValueError, match="confirmation"):
        tc.ToolContract(
            name="feishu.doc_create",
            description="create",
            input_schema={},
            permission_scope="doc.write",
            timeout_sec=10,
            max_results=1,
            source_tier=tc.SourceTier.FEISHU_LIVE,
            truth_level=tc.TruthLevel.LIVE_CONTEXT,
            output_schema={},
            side_effect=tc.SideEffect.WRITE,
            confirmation_required=False,
        )


def test_speech_hints_distinct():
    assert "周报" in tc.speech_hint(tc.SourceTier.PUBLISHED)
    assert "飞书" in tc.speech_hint(tc.SourceTier.FEISHU_LIVE)
    assert "看法" in tc.speech_hint(tc.SourceTier.MODEL)


def test_empty_result_flag():
    env = tc.ToolResultEnvelope(
        ok=True,
        tool="feishu.search",
        source_tier=tc.SourceTier.FEISHU_LIVE,
        truth_level=tc.TruthLevel.LIVE_CONTEXT,
        items=[],
    )
    assert env.empty is True
