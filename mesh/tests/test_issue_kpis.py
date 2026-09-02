"""KPI 与卡片数量一致性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.issue_verify import sync_kpis_from_data


def test_kpi_relations_count_matches_cards():
    out = sync_kpis_from_data({
        "relations": [
            {
                "title": "破壳创智",
                "teams": ["编辑部", "Global Partnership 团队"],
                "body": "x",
                "details": [],
                "sources": [],
                "label": "合作机会",
                "weak": False,
                "decision_tier": "strong",
                "evidence": [{"item_id": 1, "team": "编辑部"}],
            }
        ],
        "contacts": [],
        "keywords": {"groups": []},
    })
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
