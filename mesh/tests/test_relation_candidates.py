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
    from app.relation_decision import assign_candidate_ids, build_relations_two_phase

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
        narratives=[{"candidate_id": "c1", "title": "破壳创智", "body": "x", "details": ["编辑部记录：a", "Global Partnership 团队记录：b"]}],
    )
    titles = [r["title"] for r in out["relations"]]
    assert any("破壳" in t for t in titles)
    assert "完全编造的公司" not in titles


def test_merge_preserves_evidence_assets():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "p1", "entities": '["破壳创智"]', "text": "编辑部snippet", "source_label": "编辑部记录", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "p2", "entities": '["破壳创智"]', "text": "GP snippet", "source_label": "GP周报", "blocked": 0},
    ]
    from app.relation_decision import assign_candidate_ids, build_relations_two_phase

    cands = assign_candidate_ids(build_relation_candidates(items))
    out = build_relations_two_phase(
        {"relations": []},
        cands,
        items,
        decisions=[{
            "candidate_id": "c1",
            "decision": "keep",
            "label": "同一件事，两个部门各知一半",
            "relation_type": "info_complement",
            "reason": "编辑部与 GP 都在跟进破壳创智，两侧记录可串成联合拜访",
            "evidence_refs": [1, 2],
        }],
        narratives=[{
            "candidate_id": "c1",
            "title": "破壳创智",
            "body": "编辑部与 GP 都在跟进破壳创智，可安排一次联合拜访。",
            "details": ["编辑部记录：编辑部snippet", "Global Partnership 团队记录：GP snippet"],
        }],
    )
    rel = out["relations"][0]
    assert rel.get("label") == "同一件事，两个部门各知一半"
    assert rel.get("item_ids") == [1, 2]
    assert len(rel.get("evidence") or []) == 2
    assert rel["evidence"][0]["item_id"] in (1, 2)
    assert rel.get("provenance_ok") is True


def test_empty_draft_does_not_auto_fill_candidates():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "p1", "entities": '["破壳创智"]', "text": "a", "source_label": "编辑部", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "p2", "entities": '["破壳创智"]', "text": "b", "source_label": "GP", "blocked": 0},
    ]
    from app.relation_decision import assign_candidate_ids, build_relations_two_phase

    cands = assign_candidate_ids(build_relation_candidates(items))
    assert len(cands) >= 1
    out = build_relations_two_phase({"relations": []}, cands, items, decisions=[], narratives=[])
    assert out["relations"] == []


def test_routing_candidate_from_roles():
    items = [
        {
            "id": 10,
            "source_id": 1,
            "owner_team": "Global Partnership 团队",
            "pointer": "可灵",
            "entities": '["可灵"]',
            "roles": '["编辑部", "投资团队"]',
            "text": "GP 跟进可灵，编辑部、投资团队用得上",
            "source_label": "GP周报",
            "blocked": 0,
        },
    ]
    cands = build_relation_candidates(items)
    assert len(cands) == 1
    route = cands[0]
    assert route.get("candidate_kind") == "routing"
    assert route["weak"] is True
    assert route["teams"][0] == "Global Partnership 团队"
    assert "→ 编辑部" in route["teams"]
    assert "→ 投资团队" in route["teams"]
    assert set(route["routing_targets"]) == {"编辑部", "投资团队"}


def test_routing_merges_multiple_targets_one_card():
    """同一主体多承接方 → 一张路由候选，多个 → 团队。"""
    items = [
        {
            "id": 2,
            "source_id": 11,
            "owner_team": "Global Partnership 团队",
            "pointer": "张岩",
            "entities": '["张岩","Notta"]',
            "roles": '["投资团队","编辑部"]',
            "text": "AGI 活动嘉宾张岩，投资团队用得上，编辑部采访池用得上",
            "source_label": "GP",
            "blocked": 0,
        },
    ]
    cands = build_relation_candidates(items)
    routes = [c for c in cands if c.get("candidate_kind") == "routing"]
    assert len(routes) == 1
    teams = routes[0]["teams"]
    assert teams[0] == "Global Partnership 团队"
    assert "→ 投资团队" in teams
    assert "→ 编辑部" in teams
    assert set(routes[0]["routing_targets"]) == {"投资团队", "编辑部"}


def test_routing_kept_alongside_cooccurrence():
    """共现与路由可并存：Decision 再判是否重复/保留。"""
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "破壳", "entities": '["破壳创智"]', "roles": '[]', "text": "编辑部沟通破壳", "source_label": "编辑部", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "破壳GP", "entities": '["破壳创智"]', "roles": '["编辑部"]', "text": "GP拟拜访，编辑部用得上", "source_label": "GP", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    kinds = {c.get("candidate_kind") for c in cands}
    assert "cooccurrence" in kinds
    assert "routing" in kinds
    assert len(cands) >= 2


def test_entity_key_cooccurrence_normalizes_spaces_case():
    """FieldAI ↔ Field AI 不同 source → 一条共现。"""
    items = [
        {
            "id": 1,
            "source_id": 10,
            "owner_team": "编辑部",
            "pointer": "f1",
            "entities": '["FieldAI"]',
            "text": "编辑部提到 FieldAI",
            "source_label": "编辑部",
            "blocked": 0,
        },
        {
            "id": 2,
            "source_id": 11,
            "owner_team": "Global Partnership 团队",
            "pointer": "f2",
            "entities": '["Field AI"]',
            "text": "GP 跟进 Field AI",
            "source_label": "GP",
            "blocked": 0,
        },
    ]
    cands = build_relation_candidates(items)
    cooc = [c for c in cands if c.get("candidate_kind") == "cooccurrence"]
    assert len(cooc) == 1
    assert set(cooc[0]["teams"]) == {"编辑部", "Global Partnership 团队"}


def test_card_bridge_weak_candidate():
    """卡面写已沟通 Reverie AI，他队 item 有同实体 → card_bridge。"""
    items = [
        {
            "id": 42,
            "source_id": 7,
            "owner_team": "编辑部",
            "pointer": "rev",
            "entities": '["Reverie AI"]',
            "text": "编辑部侧提到 Reverie AI",
            "source_label": "编辑部记录",
            "blocked": 0,
        },
    ]
    cards = [
        {
            "team": "硅谷 BD 团队",
            "sections": [
                {
                    "title": "在跟进的合作与团队",
                    "lines": [
                        "已沟通两次：Reverie AI（实时交互模型），已约下一步",
                    ],
                }
            ],
        }
    ]
    cands = build_relation_candidates(items, team_cards=cards)
    bridges = [c for c in cands if c.get("candidate_kind") == "card_bridge"]
    assert len(bridges) == 1
    b = bridges[0]
    assert b["weak"] is True
    assert b["teams"][0] == "硅谷 BD 团队"
    assert "→ 编辑部" in b["teams"]
    assert "Reverie" in (b.get("title") or "")
    assert 42 in (b.get("item_ids") or [])


def test_card_bridge_onesided_overseas_followup():
    """实体只在卡方：已沟通叙事 → 弱桥接到编辑部。"""
    items = [
        {
            "id": 7,
            "source_id": 3,
            "owner_team": "硅谷 BD 团队",
            "pointer": "rev",
            "entities": '["Reverie AI"]',
            "text": "BD 已沟通 Reverie AI",
            "source_label": "硅谷周报",
            "blocked": 0,
        },
    ]
    cards = [
        {
            "team": "硅谷 BD 团队",
            "sections": [
                {
                    "title": "在跟进的合作与团队",
                    "lines": ["已沟通两次：Reverie AI（实时交互），已约下一步"],
                }
            ],
        }
    ]
    cands = build_relation_candidates(items, team_cards=cards)
    bridges = [c for c in cands if c.get("candidate_kind") == "card_bridge"]
    assert len(bridges) == 1
    assert bridges[0]["teams"][0] == "硅谷 BD 团队"
    assert "→ 编辑部" in bridges[0]["teams"]
    assert 7 in (bridges[0].get("item_ids") or [])


def test_no_bridge_or_cooc_from_bare_ai_substring():
    """裸 AI / 仅子串不得造共现或桥接。"""
    items = [
        {
            "id": 1,
            "source_id": 1,
            "owner_team": "编辑部",
            "pointer": "a",
            "entities": '["AI"]',
            "text": "泛谈 AI",
            "source_label": "编辑部",
            "blocked": 0,
        },
        {
            "id": 2,
            "source_id": 2,
            "owner_team": "视频号团队",
            "pointer": "b",
            "entities": '["FieldAI"]',
            "text": "FieldAI 线索",
            "source_label": "视频号",
            "blocked": 0,
        },
    ]
    cards = [
        {
            "team": "硅谷 BD 团队",
            "sections": [
                {
                    "title": "在跟进的合作与团队",
                    "lines": ["尚未接触·大模型/AI Infra：xAI"],
                }
            ],
        }
    ]
    cands = build_relation_candidates(items, team_cards=cards)
    # AI vs FieldAI 不得因归一或子串共现
    cooc = [c for c in cands if c.get("candidate_kind") == "cooccurrence"]
    assert cooc == []
    # 卡面抽到 AI 不得桥到 FieldAI；xAI 也没有对侧 item
    bridges = [c for c in cands if c.get("candidate_kind") == "card_bridge"]
    assert bridges == []


if __name__ == "__main__":
    test_no_candidate_same_source()
    test_candidate_different_sources()
    test_merge_strips_hallucinated_relation()
    test_merge_preserves_evidence_assets()
    test_routing_merges_multiple_targets_one_card()
    test_entity_key_cooccurrence_normalizes_spaces_case()
    test_card_bridge_weak_candidate()
    test_no_bridge_or_cooc_from_bare_ai_substring()
    print("ok")
