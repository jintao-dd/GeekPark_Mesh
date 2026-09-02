"""KPI 与卡片数量一致性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.issue_verify import sync_kpis_from_data


def _strong(title: str) -> dict:
    return {
        "title": title,
        "body": "x",
        "decision_tier": "strong",
        "evidence": [{"item_id": 1}],
        "label": "已联动",
        "teams": ["编辑部"],
        "details": [],
        "sources": [],
    }


def test_kpi_relations_count_matches_cards():
    out = sync_kpis_from_data({
        "relations": [_strong("破壳创智")],
        "contacts": [],
        "keywords": {"groups": []},
    })
    kpi_rel = next(k for k in out["kpis"] if k["label"] == "可同步的关系")
    assert kpi_rel["n"] == "1"
    assert len(out["relations"]) == 1


def test_kpi_relations_counts_reader_only_not_backlog():
    """KPI 对齐读者卡：parallel/watch 进草稿积压，不计 KPI。"""
    out = sync_kpis_from_data({
        "relations": [
            _strong("S1"),
            _strong("S2"),
            {
                "title": "P",
                "body": "b",
                "decision_tier": "parallel",
                "evidence": [{}],
                "label": "同一赛道，各自在做",
            },
            {
                "title": "W",
                "body": "b",
                "decision_tier": "watch",
                "evidence": [{}],
                "label": "一方有需求，另一方尚未接触",
            },
        ],
        "contacts": [],
        "keywords": {"groups": []},
    })
    assert next(k for k in out["kpis"] if k["label"] == "可同步的关系")["n"] == "2"


def test_sync_kpis_founder_dialogue_from_contacts():
    data = sync_kpis_from_data({
        "relations": [_strong("a"), _strong("b")],
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
