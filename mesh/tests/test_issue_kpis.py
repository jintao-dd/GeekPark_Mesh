"""KPI 与卡片数量一致性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.issue_verify import sync_kpis_from_data
from app.relation_candidates import build_relation_candidates, merge_relations_from_candidates


def test_kpi_relations_count_matches_cards():
    items = [
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "p1", "entities": '["破壳创智"]', "text": "a", "source_label": "编辑部", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "p2", "entities": '["破壳创智"]', "text": "b", "source_label": "GP", "blocked": 0},
    ]
    cands = build_relation_candidates(items)
    draft = {
        "kpis": [{"n": "99", "label": "可同步的关系"}],
        "relations": [
            {
                "title": "破壳创智",
                "teams": ["编辑部", "Global Partnership 团队"],
                "body": "x",
                "details": [],
                "sources": [],
                "label": "合作机会",
                "weak": False,
            }
        ],
        "contacts": [],
        "keywords": {"groups": []},
    }
    out = merge_relations_from_candidates(draft, cands, items)
    kpi_rel = next(k for k in out["kpis"] if k["label"] == "可同步的关系")
    assert kpi_rel["n"] == "1"
    assert len(out["relations"]) == 1


def test_sync_kpis_founder_dialogue_from_contacts():
    data = sync_kpis_from_data({
        "relations": [{"title": "a"}, {"title": "b"}],
        "contacts": [
            {
                "label": "编辑部一手对话 · 3 场",
                "title": "创始人与高管",
                "groups": [{"items": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}],
            }
        ],
        "keywords": {"groups": [{"items": [{"name": "k1"}, {"name": "k2"}]}]},
    })
    assert next(k for k in data["kpis"] if k["label"] == "可同步的关系")["n"] == "2"
    assert next(k for k in data["kpis"] if k["label"] == "创始人级一手对话")["n"] == "3"
    assert next(k for k in data["kpis"] if k["label"] == "关注的事")["n"] == "2"
