"""Attribution 归属语义单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.aggregator import SplitResult, Segment, sources_from_split
from app.attribution import (
    PROVENANCE_LLM_HINT,
    PROVENANCE_MANUAL,
    PROVENANCE_SEGMENT,
    PROVENANCE_UNKNOWN,
    apply_attribution_to_item,
    resolve_attribution,
)


def test_manual_beats_segment_and_llm():
    """用户选编辑部，段推断/LLM 标硅谷 BD → owner 仍为编辑部。"""
    it = {
        "llm_owner_team_hint": "硅谷 BD 团队",
        "source_label": "硅谷 BD 团队 · 詹杨帆",
        "text": "x",
    }
    attr = resolve_attribution(
        it,
        source_team="编辑部 · 沟通记录",
        segment_team="硅谷 BD 团队",
    )
    assert attr.owner_team == "编辑部"
    assert attr.provenance == PROVENANCE_MANUAL


def test_segment_when_placeholder_source():
    it = {"llm_owner_team_hint": "编辑部", "text": "x"}
    attr = resolve_attribution(
        it,
        source_team="内容中心·数据聚合",
        segment_team="编辑部",
    )
    assert attr.owner_team == "编辑部"
    assert attr.provenance == PROVENANCE_SEGMENT


def test_llm_hint_never_becomes_owner():
    it = {"llm_owner_team_hint": "编辑部", "text": "x"}
    attr = resolve_attribution(it, source_team="内容中心·数据聚合", segment_team=None)
    assert attr.owner_team is None
    assert attr.provenance == PROVENANCE_LLM_HINT
    assert attr.block is True
    assert attr.llm_owner_team_hint == "编辑部"


def test_unknown_when_no_signal():
    it = {"text": "x"}
    attr = resolve_attribution(it, source_team="内容中心·数据聚合", segment_team=None)
    assert attr.owner_team is None
    assert attr.provenance == PROVENANCE_UNKNOWN
    assert attr.block is True


def test_apply_attribution_blocks_unresolved():
    it = {"text": "x", "blocked": 0}
    apply_attribution_to_item(it, source_team="内容中心·数据聚合", segment_team=None)
    assert it["blocked"] == 1
    assert it.get("_owner_needs_review") is True


def test_sources_from_split_respects_manual_upload():
    split = SplitResult(
        segments=[
            Segment(title="硅谷 BD · 建联", text="bbb", stype="T3", team="硅谷 BD 团队", owner_hint="硅谷 BD 团队"),
        ],
        mode="multi",
        boundaries=1,
    )
    rows = sources_from_split(
        split,
        parent_title="数据聚合",
        parent_filename="agg.docx",
        upload_team="编辑部 · 沟通记录",
    )
    assert len(rows) == 1
    assert rows[0]["team"] == "编辑部 · 沟通记录"
    assert rows[0]["meta"]["split"]["segment_inferred_team"] == "硅谷 BD 团队"
    assert rows[0]["meta"]["split"]["upload_team_override"] == "编辑部 · 沟通记录"


def test_sources_from_split_placeholder_uses_segment():
    split = SplitResult(
        segments=[
            Segment(title="编辑部 · 沟通", text="aaa", stype="T1", team="编辑部", owner_hint="编辑部"),
        ],
        mode="multi",
    )
    rows = sources_from_split(
        split,
        parent_title="数据聚合",
        parent_filename="agg.docx",
        upload_team="内容中心·数据聚合",
    )
    assert rows[0]["team"] == "编辑部"
    assert "segment_inferred_team" not in rows[0]["meta"]["split"]
