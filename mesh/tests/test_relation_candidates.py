"""relation_candidates 单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_candidates import (
    build_relation_candidates,
    merge_relations_from_candidates,
)


def test_no_candidate_same_source():
    items = [
        {"id": 1, "source_id": 23, "owner_team": "编辑部", "pointer": "面壁—詹", "entities": '["詹杨帆"]', "text": "a", "source_label": "编辑部沟通", "blocked": 0},
        {"id": 2, "source_id": 23, "owner_team": "硅谷 BD 团队", "pointer": "面壁—詹", "entities": '["詹杨帆"]', "text": "b", "source_label": "误标", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    assert cands == []


def test_candidate_different_sources():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "破壳", "entities": '["破壳创智"]', "text": "编辑部沟通破壳", "source_label": "编辑部沟通记录", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "破壳GP", "entities": '["破壳创智"]', "text": "GP拟拜访", "source_label": "GP工作周报", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    assert len(cands) == 1
    assert set(cands[0]["teams"]) == {"编辑部", "Global Partnership 团队"}


def test_merge_strips_hallucinated_relation():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "破壳", "entities": '["破壳创智"]', "text": "a", "source_label": "编辑部", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "破壳GP", "entities": '["破壳创智"]', "text": "b", "source_label": "GP", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    draft = {
        "relations": [
            {"title": "破壳创智", "teams": ["编辑部", "Global Partnership 团队"], "body": "x", "details": ["a", "b"], "sources": [], "label": "两处记录待核对", "weak": False},
            {"title": "完全编造的公司", "teams": ["编辑部", "硅谷 BD 团队"], "body": "假", "details": [], "sources": [], "label": "一方接触，另一方用得上", "weak": False},
        ]
    }
    out = merge_relations_from_candidates(draft, cands, items)
    titles = [r["title"] for r in out["relations"]]
    assert "破壳创智" in titles[0] or any("破壳" in t for t in titles)
    assert "完全编造的公司" not in titles


def test_merge_preserves_evidence_assets():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "p1", "entities": '["破壳创智"]', "text": "编辑部snippet", "source_label": "编辑部记录", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "p2", "entities": '["破壳创智"]', "text": "GP snippet", "source_label": "GP周报", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    draft = {
        "relations": [
            {
                "title": "破壳创智",
                "teams": ["编辑部", "Global Partnership 团队"],
                "body": "编辑部与 GP 都在跟进破壳创智，可安排一次联合拜访。",
                "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
                "sources": ["编辑部记录", "GP周报"],
                "label": "合作机会",
                "weak": False,
            }
        ]
    }
    out = merge_relations_from_candidates(draft, cands, items)
    rel = out["relations"][0]
    assert rel.get("label") == "合作机会"
    assert "联合拜访" in rel.get("body") or "破壳" in rel.get("body")
    assert rel.get("item_ids") == [1, 2]
    assert len(rel.get("evidence") or []) == 2
    assert rel["evidence"][0]["item_id"] in (1, 2)
    assert rel["evidence"][0].get("source_id") in (10, 11)
    assert rel["evidence"][0].get("team")
    assert rel["evidence"][0].get("snippet")
    assert rel.get("relation_type")
    assert rel.get("provenance_ok") is True


def test_empty_draft_does_not_auto_fill_candidates():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "p1", "entities": '["破壳创智"]', "text": "a", "source_label": "编辑部", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "p2", "entities": '["破壳创智"]', "text": "b", "source_label": "GP", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    assert len(cands) >= 1
    out = merge_relations_from_candidates({"relations": []}, cands, items)
    assert out["relations"] == []


if __name__ == "__main__":
    test_no_candidate_same_source()
    test_candidate_different_sources()
    test_merge_strips_hallucinated_relation()
    test_merge_preserves_evidence_assets()
    print("ok")
