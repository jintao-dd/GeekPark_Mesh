"""owner_guard 单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.attribution import resolve_attribution
from app.owner_guard import (
    apply_item_owner_guards,
    cross_team_provenance_ok,
    filter_draft_relations,
    parse_section_team,
    relation_team_supported,
    resolve_item_owner,
)


def test_parse_section_team():
    assert parse_section_team("编辑部 · 沟通记录") == "编辑部"
    assert parse_section_team("硅谷 BD 团队 · 建联") == "硅谷 BD 团队"
    assert parse_section_team("随机标题") is None


def test_manual_beats_segment_on_agg_source():
    """无 manual 时 segment 生效；有 manual 时 segment 不得覆盖。"""
    it = {"llm_owner_team_hint": "硅谷 BD 团队", "text": "x"}
    assert resolve_item_owner(it, source_team="内容中心·数据聚合", segment_team="编辑部") == "编辑部"

    it2 = {"llm_owner_team_hint": "硅谷 BD 团队", "text": "x"}
    assert resolve_item_owner(it2, source_team="编辑部 · 沟通记录", segment_team="硅谷 BD 团队") == "编辑部"


def test_resolve_source_team_over_llm():
    """上传页选定的团队优先于 LLM hint。"""
    it = {"llm_owner_team_hint": "硅谷 BD 团队", "text": "x"}
    assert resolve_item_owner(it, source_team="视频号团队", segment_team=None) == "视频号团队"


def test_resolve_editorial_pick_to_owner():
    it = {"llm_owner_team_hint": "硅谷 BD 团队", "text": "x"}
    assert resolve_item_owner(it, source_team="编辑部 · 选题表", segment_team=None) == "编辑部"


def test_agg_llm_hint_does_not_become_owner():
    it = {"llm_owner_team_hint": "编辑部", "text": "x"}
    assert resolve_item_owner(it, source_team="内容中心·数据聚合", segment_team=None) is None
    attr = resolve_attribution(it, source_team="内容中心·数据聚合", segment_team=None)
    assert attr.block is True


def test_dedupe_same_pointer():
    items = [
        {"pointer": "面壁智能—詹杨帆", "owner_team": "编辑部", "_segment_team": "编辑部", "entities": ["詹杨帆"], "blocked": 0},
        {"pointer": "面壁智能—詹杨帆", "owner_team": "硅谷 BD 团队", "_segment_team": "编辑部", "entities": ["詹杨帆"], "blocked": 0},
    ]
    out = apply_item_owner_guards(items)
    active = [x for x in out if not x.get("blocked")]
    blocked = [x for x in out if x.get("blocked")]
    assert len(active) == 1
    assert active[0]["owner_team"] == "编辑部"
    assert len(blocked) == 1


def test_provenance_key_includes_source_id():
    from app.owner_guard import provenance_key
    a = provenance_key({"source_id": 1, "pointer": "面壁—詹杨帆"})
    b = provenance_key({"source_id": 2, "pointer": "面壁—詹杨帆"})
    assert a != b
    assert a.startswith("s1:")


def test_cross_team_same_source_false():
    items = [
        {"id": 1, "source_id": 23, "owner_team": "编辑部", "pointer": "面壁智能—詹杨帆", "entities": '["詹杨帆"]', "blocked": 0},
        {"id": 2, "source_id": 23, "owner_team": "硅谷 BD 团队", "pointer": "面壁智能—詹杨帆", "entities": '["詹杨帆"]', "blocked": 0},
    ]
    assert not cross_team_provenance_ok(items, "面壁智能 · 詹杨帆", ["编辑部", "硅谷 BD 团队"])


def test_filter_draft_relations():
    items = [
        {"id": 1, "source_id": 23, "owner_team": "编辑部", "pointer": "面壁智能—詹杨帆", "entities": '["詹杨帆","面壁智能"]', "blocked": 0},
        {"id": 2, "source_id": 23, "owner_team": "硅谷 BD 团队", "pointer": "面壁智能—詹杨帆", "entities": '["詹杨帆"]', "blocked": 1},
    ]
    draft = {
        "relations": [{
            "label": "一方接触，另一方用得上",
            "weak": False,
            "title": "面壁智能 · 詹杨帆",
            "body": "编辑部与硅谷 BD 团队各有一手对话记录。",
            "details": [
                "编辑部记录：端云协同。",
                "硅谷 BD 团队记录：终端是 AI 进入物理世界的路径。",
            ],
            "teams": ["编辑部", "硅谷 BD 团队"],
        }]
    }
    out = filter_draft_relations(draft, items)
    assert len(out["relations"]) == 1
    rel = out["relations"][0]
    assert "硅谷 BD" not in " ".join(rel.get("details") or [])
    assert rel.get("weak") is True


def test_relation_team_supported_by_evidence_item_owner():
    """优先 evidence.item_id → owner_team，不依赖标题词面命中。"""
    items = [
        {"id": 10, "owner_team": "编辑部", "entities": '["无关实体"]', "blocked": 0, "text": "x"},
        {"id": 11, "owner_team": "商业化团队", "entities": '["另一实体"]', "blocked": 0, "text": "y"},
    ]
    rel = {
        "title": "跨团队协作主题",
        "candidate_title": "跨团队协作主题",
        "teams": ["编辑部", "商业化团队"],
        "evidence": [{"item_id": 10}, {"item_id": 11}],
    }
    assert relation_team_supported(items, rel, "编辑部")
    assert relation_team_supported(items, rel, "商业化团队")
    assert not relation_team_supported(items, rel, "视频号团队")


def test_relation_team_supported_fallback_title_entity():
    items = [
        {"id": 1, "owner_team": "编辑部", "entities": '["面壁智能"]', "blocked": 0, "text": "面壁"},
    ]
    rel = {
        "title": "面壁智能 · 合作",
        "teams": ["编辑部"],
        "evidence": [{"source": "legacy"}],
    }
    assert relation_team_supported(items, rel, "编辑部")


if __name__ == "__main__":
    test_parse_section_team()
    test_manual_beats_segment_on_agg_source()
    test_resolve_source_team_over_llm()
    test_resolve_editorial_pick_to_owner()
    test_agg_llm_hint_does_not_become_owner()
    test_dedupe_same_pointer()
    test_cross_team_same_source_false()
    test_filter_draft_relations()
    test_relation_team_supported_by_evidence_item_owner()
    test_relation_team_supported_fallback_title_entity()
    print("ok")
