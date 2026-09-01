"""Phase A+ relation merge：事实一致性 / 无猜测挂载 / 无重复。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_candidates import (
    build_relation_candidates,
    merge_relations_from_candidates,
    _strict_title_match,
)


def _pok_items():
    return [
        {
            "id": 1,
            "source_id": 10,
            "owner_team": "编辑部",
            "pointer": "p1",
            "entities": '["破壳创智"]',
            "text": "编辑部snippet",
            "source_label": "编辑部记录",
            "blocked": 0,
        },
        {
            "id": 2,
            "source_id": 11,
            "owner_team": "Global Partnership 团队",
            "pointer": "p2",
            "entities": '["破壳创智"]',
            "text": "GP snippet",
            "source_label": "GP周报",
            "blocked": 0,
        },
    ]


def test_strict_title_match_rejects_single_entity_guess():
    assert not _strict_title_match("豆包", "字节视频 · 豆包")
    assert _strict_title_match("豆包 · 字节跳动", "豆包 · 字节跳动")


def test_no_duplicate_external_weak():
    items = [
        {
            "id": 1,
            "source_id": 1,
            "owner_team": "编辑部",
            "pointer": "p",
            "entities": '["外部话题"]',
            "text": "x",
            "source_label": "编辑部记录",
            "blocked": 0,
        },
    ]
    draft = {
        "relations": [
            {
                "title": "外部话题",
                "weak": True,
                "body": "外部媒体报道中",
                "label": "外部在热聊",
                "teams": ["编辑部"],
            }
        ]
    }
    out = merge_relations_from_candidates(draft, [], items)
    assert len(out["relations"]) == 1
    teams = out["relations"][0].get("teams") or []
    assert teams and not any(str(t).startswith("→") for t in teams)


def test_teams_and_sources_from_evidence_not_llm():
    items = _pok_items()
    cands = build_relation_candidates(items)
    draft = {
        "relations": [
            {
                "title": "破壳创智",
                "teams": ["视频号团队", "硅谷 BD 团队"],
                "sources": ["外部媒体 · 假来源"],
                "body": "编辑叙事",
                "details": ["视频号团队记录：假"],
                "label": "合作机会",
                "weak": False,
            }
        ]
    }
    out = merge_relations_from_candidates(draft, cands, items)
    rel = out["relations"][0]
    assert set(rel["teams"]) == {"编辑部", "Global Partnership 团队"}
    assert "假来源" not in " ".join(rel.get("sources") or [])
    assert all(s in {"编辑部记录", "GP周报"} for s in rel.get("sources") or [])


def test_llm_only_without_candidate_dropped():
    items = _pok_items()
    cands = build_relation_candidates(items)
    draft = {
        "relations": [
            {
                "title": "完全无候选的新故事",
                "teams": ["编辑部", "Global Partnership 团队"],
                "body": "LLM 编造",
                "label": "合作机会",
                "weak": False,
            }
        ]
    }
    out = merge_relations_from_candidates(draft, cands, items)
    assert out["relations"] == []


def test_no_guess_match_different_entity_sets():
    items = [
        {
            "id": 1,
            "source_id": 10,
            "owner_team": "编辑部",
            "pointer": "a",
            "entities": '["豆包", "字节跳动"]',
            "text": "a",
            "source_label": "编辑部",
            "blocked": 0,
        },
        {
            "id": 2,
            "source_id": 11,
            "owner_team": "视频号团队",
            "pointer": "b",
            "entities": '["豆包", "字节跳动"]',
            "text": "b",
            "source_label": "视频号",
            "blocked": 0,
        },
        {
            "id": 3,
            "source_id": 12,
            "owner_team": "编辑部",
            "pointer": "c",
            "entities": '["豆包", "模型"]',
            "text": "c",
            "source_label": "编辑部2",
            "blocked": 0,
        },
        {
            "id": 4,
            "source_id": 13,
            "owner_team": "视频号团队",
            "pointer": "d",
            "entities": '["豆包", "模型"]',
            "text": "d",
            "source_label": "视频号2",
            "blocked": 0,
        },
    ]
    cands = build_relation_candidates(items)
    titles = {c["title"] for c in cands}
    assert any("字节跳动" in t for t in titles)
    draft = {
        "relations": [
            {
                "title": "字节视频/音频模型 · 豆包",
                "teams": ["编辑部", "视频号团队"],
                "body": "x",
                "label": "同一件事，两个部门各知一半",
                "weak": False,
            }
        ]
    }
    out = merge_relations_from_candidates(draft, cands, items)
    assert out["relations"] == []


def test_evidence_team_source_consistency():
    items = _pok_items()
    cands = build_relation_candidates(items)
    draft = {
        "relations": [
            {
                "title": "破壳创智",
                "teams": ["编辑部", "Global Partnership 团队"],
                "body": "联合跟进",
                "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
                "sources": ["编辑部记录", "GP周报"],
                "label": "合作机会",
                "weak": False,
            }
        ]
    }
    out = merge_relations_from_candidates(draft, cands, items)
    rel = out["relations"][0]
    ev_teams = {e["team"] for e in rel["evidence"]}
    solid = {t for t in rel["teams"] if not str(t).startswith("→")}
    assert solid == ev_teams
    ev_labels = {e["source_label"] for e in rel["evidence"]}
    assert set(rel["sources"]) <= ev_labels
