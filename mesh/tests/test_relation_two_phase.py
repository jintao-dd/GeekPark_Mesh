"""两阶段关系：Decision → Gate → Narrative。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_candidates import build_relation_candidates, merge_relations_from_candidates
from app.relation_decision import (
    apply_evidence_gate,
    assign_candidate_ids,
    build_relations_two_phase,
    is_pure_entity_cooccurrence,
)
from app.relation_decision_audit import build_human_report, finalize_decision_audit


def _pok_items():
    return [
        {
            "id": 1,
            "source_id": 10,
            "owner_team": "编辑部",
            "pointer": "p1",
            "entities": '["破壳创智"]',
            "text": "编辑部跟进中：破壳创智选题推进",
            "source_label": "编辑部记录",
            "blocked": 0,
        },
        {
            "id": 2,
            "source_id": 11,
            "owner_team": "Global Partnership 团队",
            "pointer": "p2",
            "entities": '["破壳创智"]',
            "text": "GP 已沟通破壳创智，对接中",
            "source_label": "GP周报",
            "blocked": 0,
        },
    ]


def test_pure_entity_cooccurrence_warned_and_backlogged():
    """千问 · Claude 类：次实体仅一方 snippet 命中 → 纯共现 → 不再硬 skip，改为 warning 并 backlog。"""
    cand = {
        "candidate_id": "c1",
        "title": "千问 · Claude Opus 4.5",
        "teams": ["商业化团队", "视频号团队"],
        "item_ids": [10, 20],
        "team_facts": [
            {"team": "商业化团队", "item_ids": [10], "snippets": ["阿里云千问签约排期已定"]},
            {"team": "视频号团队", "item_ids": [20], "snippets": ["豆包模型与 Claude Opus 4.5 同场对比"]},
        ],
    }
    assert is_pure_entity_cooccurrence(cand)
    decisions = [{
        "candidate_id": "c1",
        "decision": "keep",
        "label": "两个部门各有判断",
        "relation_type": "parallel_tracks",
        "decision_tier": "parallel",
        "reason": "双团队记录同一话题，商业化与视频号各有片段",
        "evidence_refs": [10, 20],
    }]
    items = [
        {"id": 10, "source_id": 1, "owner_team": "商业化团队", "pointer": "a", "entities": '["千问"]', "text": "千问", "source_label": "商业化", "blocked": 0},
        {"id": 20, "source_id": 2, "owner_team": "视频号团队", "pointer": "b", "entities": '["Claude"]', "text": "Claude", "source_label": "视频号", "blocked": 0},
    ]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    # 2026-09 框架调整：纯共现不再硬 skip，改为放行但降级为 watch/backlog
    assert len(approved) == 1
    assert approved[0]["decision_tier"] == "watch"
    assert "pure_entity_cooccurrence" in approved[0].get("_gate_warnings", [])
    assert audit["rows"][0]["gate_decision"] == "keep"
    assert audit["rows"][0]["gate_reason_code"] == "pure_entity_cooccurrence"
    assert audit["rows"][0]["gate_would_cooccur"] is True


def test_no_shared_anchor_warned_and_backlogged():
    """两侧无共享实体锚点（拼盘公司）→ 不再硬 skip，改为 warning 并 backlog。"""
    from app.relation_decision import evidence_shared_anchor

    ev = [
        {"team": "编辑部", "snippet": "郭仁杰公司新模型采访提纲已发", "item_id": 1},
        {"team": "英文站", "snippet": "Seeed Studio Eric Pan 访谈已发布", "item_id": 2},
    ]
    assert not evidence_shared_anchor(ev, title="郭仁杰公司新模型稿与 Seeed Studio 访谈")
    cand = {
        "candidate_id": "c2",
        "title": "郭仁杰公司与 Seeed Studio",
        "teams": ["编辑部", "英文站"],
        "item_ids": [1, 2],
        "team_facts": [
            {"team": "编辑部", "item_ids": [1], "snippets": ["郭仁杰公司新模型采访提纲已发"]},
            {"team": "英文站", "item_ids": [2], "snippets": ["Seeed Studio Eric Pan 访谈已发布"]},
        ],
    }
    decisions = [{
        "candidate_id": "c2",
        "decision": "keep",
        "label": "同一公司，不同触点",
        "relation_type": "parallel_tracks",
        "decision_tier": "parallel",
        "reason": "两队各有记录",
        "evidence_refs": [1, 2],
    }]
    items = [
        {"id": 1, "source_id": 1, "owner_team": "编辑部", "pointer": "a", "entities": '["郭仁杰"]', "text": "郭仁杰公司新模型采访提纲已发", "source_label": "编辑部", "blocked": 0},
        {"id": 2, "source_id": 2, "owner_team": "英文站", "pointer": "b", "entities": '["Seeed"]', "text": "Seeed Studio Eric Pan 访谈已发布", "source_label": "英文站", "blocked": 0},
    ]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    # 2026-09 框架调整：无共享锚点不再硬 skip，改为放行但降级为 watch/backlog
    assert len(approved) == 1
    assert approved[0]["decision_tier"] == "watch"
    assert "no_shared_anchor" in approved[0].get("_gate_warnings", [])
    assert audit["rows"][0]["gate_decision"] == "keep"
    assert audit["rows"][0]["gate_reason_code"] == "no_shared_anchor"



def test_decision_inconsistent_warns_but_does_not_block():
    """parallel +「非同一事件」是合法表述；一致性不再 hard-block Gate。"""
    from app.relation_decision_consistency import check_decision_consistency

    err = check_decision_consistency(
        label="同一赛道，各自在做",
        reason="同处AI手机议题线但非同一事件",
        relation_type="parallel_tracks",
    )
    assert err is None

    cand = {
        "candidate_id": "c9",
        "title": "豆包 · 字节跳动",
        "teams": ["商业化团队", "视频号团队"],
        "item_ids": [1, 2],
        "team_facts": [
            {"team": "商业化团队", "item_ids": [1], "snippets": ["商业化跟进豆包方案，接触中"]},
            {"team": "视频号团队", "item_ids": [2], "snippets": ["视频号做豆包相关选题推进中"]},
        ],
    }
    items = [
        {"id": 1, "source_id": 1, "owner_team": "商业化团队", "pointer": "a", "entities": '["豆包"]', "text": "商业化跟进豆包方案，接触中", "source_label": "商业化", "blocked": 0},
        {"id": 2, "source_id": 2, "owner_team": "视频号团队", "pointer": "b", "entities": '["豆包"]', "text": "视频号做豆包相关选题推进中", "source_label": "视频号", "blocked": 0},
    ]
    decisions = [{
        "candidate_id": "c9",
        "decision": "keep",
        "label": "同一赛道，各自在做",
        "relation_type": "parallel_tracks",
        "reason": "同处AI手机议题线但非同一事件",
        "evidence_refs": [1, 2],
    }]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    assert len(approved) == 1
    assert audit["rows"][0]["gate_decision"] == "keep"


def test_audit_ledger_llm_skip_vs_gate_override():
    audit = {
        "rows": [
            {
                "candidate_id": "c1",
                "candidate_title": "千问 · Claude",
                "llm_decision": "skip",
                "llm_reason": "非同一事链",
                "gate_decision": "skip",
                "gate_reason": "非同一事链",
            },
            {
                "candidate_id": "c2",
                "candidate_title": "OpenAI · Anthropic",
                "llm_decision": "keep",
                "llm_reason": "同赛道",
                "gate_decision": "skip",
                "gate_reason": "pure_entity_cooccurrence",
            },
            {
                "candidate_id": "c3",
                "candidate_title": "破壳创智",
                "llm_decision": "keep",
                "llm_reason": "双团队",
                "gate_decision": "keep",
                "gate_reason": "双团队",
                "n_evidence": 2,
            },
        ],
    }
    rels = [{
        "candidate_id": "c3",
        "candidate_title": "破壳创智",
        "title": "破壳创智",
        "decision_tier": "strong",
        "reader_visible": True,
        "body": "双团队跟进",
        "evidence": [{}, {}],
    }]
    report = build_human_report(audit, rels)
    assert report["outcome_summary"]["n_skipped_llm"] == 1
    assert report["outcome_summary"]["n_skipped_gate_override"] == 1
    assert report["outcome_summary"]["n_published"] == 1
    override = report["gate_overrides"][0]
    assert override["candidate_id"] == "c2"
    assert override["needs_human_review"] is True


def test_llm_teams_overridden_by_evidence():
    items = _pok_items()
    cands = assign_candidate_ids(build_relation_candidates(items))
    decisions = [{
        "candidate_id": "c1",
        "decision": "keep",
        "label": "同一件事，两个部门各知一半",
        "relation_type": "info_complement",
        "decision_tier": "strong",
        "reason": "编辑部与 GP 各掌握破壳创智同一对象的不同侧面，信息互补",
        "evidence_refs": [1, 2],
    }]
    narratives = [{
        "candidate_id": "c1",
        "title": "破壳创智",
        "body": "编辑部与 GP 都在跟进破壳创智。",
        "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
    }]
    out = build_relations_two_phase(
        {"relations": []}, cands, items, decisions=decisions, narratives=narratives,
    )
    rel = out["relations"][0]
    assert rel["decision_tier"] == "strong"
    assert rel["relation_type"] == "info_complement"
    assert set(rel["teams"]) == {"编辑部", "Global Partnership 团队"}
    assert "假来源" not in " ".join(rel.get("sources") or [])
    solid = {t for t in rel["teams"] if not str(t).startswith("→")}
    ev_teams = {e["team"] for e in rel["evidence"]}
    assert solid == ev_teams
    assert set(rel["sources"]) <= {e["source_label"] for e in rel["evidence"]}


def test_no_evidence_relation_dropped():
    items = _pok_items()
    cands = assign_candidate_ids(build_relation_candidates(items))
    decisions = [{
        "candidate_id": "c1",
        "decision": "keep",
        "label": "一方接触，另一方用得上",
        "reason": "bad refs",
        "evidence_refs": [999],
    }]
    out = build_relations_two_phase(
        {"relations": []}, cands, items, decisions=decisions, narratives=[],
    )
    assert out["relations"] == []


def test_external_weak_not_duplicated():
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
    # 无 keep 决策；draft 若带 LLM weak 卡，两阶段最终 relations 为空
    out = build_relations_two_phase(
        {"relations": [{
            "title": "外部话题",
            "weak": True,
            "body": "外部媒体报道",
            "label": "外部在热聊，我们还没碰",
            "teams": ["编辑部"],
        }]},
        cands,
        items,
        decisions=[{"candidate_id": "c1", "decision": "skip", "label": "", "reason": "no cand", "evidence_refs": []}] if cands else [],
        narratives=[],
    )
    assert out["relations"] == []

def test_gate_locked_teams_survive_filter():
    """Narrative 标题与 candidate 不一致时，gate 锁定的 teams 不得被 filter 裁掉。"""
    from app.owner_guard import filter_draft_relations

    items = [
        {
            "id": 1468,
            "source_id": 22,
            "owner_team": "社群",
            "pointer": "Part 2",
            "entities": '["Founder Park","AGI"]',
            "text": "海外 Substack 内容",
            "source_label": "社群运营与内容例会提报",
            "blocked": 0,
        },
        {
            "id": 1450,
            "source_id": 26,
            "owner_team": "视频号团队",
            "pointer": "15:37",
            "entities": '["Founder Park","AGI"]',
            "text": "方正帕克 AGI",
            "source_label": "视频号周数据",
            "blocked": 0,
        },
    ]
    rel = {
        "candidate_id": "c1",
        "candidate_title": "Founder Park · AGI",
        "title": "Founder Park AGI 内容：社群海外铺内容，视频号提炼短视频",
        "body": "围绕 Founder Park 的 AGI 相关内容。",
        "label": "已联动",
        "provenance_ok": True,
        "teams": ["社群", "视频号团队"],
        "sources": ["社群运营与内容例会提报", "视频号周数据"],
        "evidence": [
            {"item_id": 1468, "team": "社群", "source_label": "社群运营与内容例会提报", "snippet": "海外 Substack"},
            {"item_id": 1450, "team": "视频号团队", "source_label": "视频号周数据", "snippet": "方正帕克 AGI"},
        ],
        "details": [],
    }
    out = filter_draft_relations({"relations": [rel]}, items)
    kept = out["relations"][0]
    solid = {t for t in kept["teams"] if not str(t).startswith("→")}
    assert solid == {"社群", "视频号团队"}


def test_approved_teams_sources_match_evidence():
    items = _pok_items()
    cands = assign_candidate_ids(build_relation_candidates(items))
    decisions = [{
        "candidate_id": "c1",
        "decision": "keep",
        "label": "两处记录待核对",
        "reason": "ok",
        "evidence_refs": [1, 2],
    }]
    narratives = [{
        "candidate_id": "c1",
        "title": "破壳创智",
        "body": "联合跟进",
        "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
    }]
    out = build_relations_two_phase(
        {"relations": []}, cands, items, decisions=decisions, narratives=narratives,
    )
    rel = out["relations"][0]
    ev_teams = {e["team"] for e in rel["evidence"]}
    solid = {t for t in rel["teams"] if not str(t).startswith("→")}
    assert solid == ev_teams
    assert set(rel["sources"]) == {e["source_label"] for e in rel["evidence"]}


def test_gate_allows_routing_single_team_as_backlog():
    """路由候选：仅 owner 有 evidence，但对侧为建议队 → 允许成卡但降级为 backlog/watch。"""
    cand = {
        "candidate_id": "c1",
        "title": "可灵",
        "teams": ["Global Partnership 团队", "→ 编辑部"],
        "weak": True,
        "candidate_kind": "routing",
        "routing_targets": ["编辑部"],
        "item_ids": [10],
        "team_facts": [{
            "team": "Global Partnership 团队",
            "item_ids": [10],
            "snippets": ["GP 跟进可灵，编辑部用得上"],
        }],
    }
    decisions = [{
        "candidate_id": "c1",
        "decision": "keep",
        "label": "一方接触，另一方用得上",
        "relation_type": "one_sided",
        "decision_tier": "watch",
        "reason": "GP 有接触，编辑部可对齐",
        "evidence_refs": [10],
    }]
    items = [{
        "id": 10,
        "source_id": 1,
        "owner_team": "Global Partnership 团队",
        "pointer": "可灵",
        "entities": '["可灵"]',
        "text": "GP 跟进可灵",
        "source_label": "GP周报",
        "blocked": 0,
    }]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    # 2026-09 框架调整：单边 watch/海外路由允许成卡，但只进 backlog 不上读者页
    assert len(approved) == 1
    assert approved[0]["decision_tier"] == "watch"
    assert approved[0].get("weak") is True
    assert audit["rows"][0]["gate_decision"] == "keep"
    assert audit["rows"][0]["gate_reason_code"] == "no_shared_anchor"

