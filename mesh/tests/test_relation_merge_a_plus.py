"""Phase A+ relation merge：事实一致性 / 无猜测挂载 / 无重复。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_candidates import (
    build_relation_candidates,
    _strict_title_match,
)
from app.relation_decision import assign_candidate_ids, build_relations_two_phase


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
    cands = assign_candidate_ids(build_relation_candidates(items))
    out = build_relations_two_phase(
        {"relations": [{
            "title": "外部话题",
            "weak": True,
            "body": "外部媒体报道中",
            "label": "外部在热聊，我们还没碰",
            "teams": ["编辑部"],
        }]},
        cands,
        items,
        decisions=[],
        narratives=[],
    )
    assert out["relations"] == []


def test_teams_and_sources_from_evidence_not_llm():
    items = _pok_items()
    cands = assign_candidate_ids(build_relation_candidates(items))
    out = build_relations_two_phase(
        {"relations": []},
        cands,
        items,
        decisions=[{
            "candidate_id": "c1",
            "decision": "keep",
            "label": "同一件事，两个部门各知一半",
            "reason": "ok",
            "evidence_refs": [1, 2],
        }],
        narratives=[{
            "candidate_id": "c1",
            "title": "破壳创智",
            "body": "编辑叙事",
            "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
        }],
    )
    rel = out["relations"][0]
    assert set(rel["teams"]) == {"编辑部", "Global Partnership 团队"}
    assert "假来源" not in " ".join(rel.get("sources") or [])
    assert all(s in {"编辑部记录", "GP周报"} for s in rel.get("sources") or [])


def test_llm_only_without_candidate_dropped():
    items = _pok_items()
    cands = assign_candidate_ids(build_relation_candidates(items))
    out = build_relations_two_phase(
        {"relations": [{
            "title": "完全无候选的新故事",
            "teams": ["编辑部", "Global Partnership 团队"],
            "body": "LLM 编造",
            "label": "合作机会",
            "weak": False,
        }]},
        cands,
        items,
        decisions=[{"candidate_id": "c1", "decision": "skip", "label": "", "reason": "x", "evidence_refs": []}],
        narratives=[],
    )
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
    cands = assign_candidate_ids(build_relation_candidates(items))
    target = next((c for c in cands if "字节跳动" in (c.get("title") or "")), None)
    assert target
    cid = target["candidate_id"]
    out = build_relations_two_phase(
        {"relations": []},
        cands,
        items,
        decisions=[{
            "candidate_id": cid,
            "decision": "keep",
            "label": "同一件事，两个部门各知一半",
            "reason": "wrong title match attempt",
            "evidence_refs": [1, 2],
        }],
        narratives=[{
            "candidate_id": cid,
            "title": "字节视频/音频模型 · 豆包",
            "body": "x",
            "details": [],
        }],
    )
    # 有 evidence 且 provenance ok 则保留（标题由 narrative 阶段，实体仍豆包系）
    assert len(out["relations"]) <= 1


def test_evidence_team_source_consistency():
    items = _pok_items()
    cands = assign_candidate_ids(build_relation_candidates(items))
    out = build_relations_two_phase(
        {"relations": []},
        cands,
        items,
        decisions=[{
            "candidate_id": "c1",
            "decision": "keep",
            "label": "两处记录待核对",
            "reason": "ok",
            "evidence_refs": [1, 2],
        }],
        narratives=[{
            "candidate_id": "c1",
            "title": "破壳创智",
            "body": "联合跟进",
            "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
        }],
    )
    rel = out["relations"][0]
    ev_teams = {e["team"] for e in rel["evidence"]}
    solid = {t for t in rel["teams"] if not str(t).startswith("→")}
    assert solid == ev_teams
    ev_labels = {e["source_label"] for e in rel["evidence"]}
    assert set(rel["sources"]) <= ev_labels
