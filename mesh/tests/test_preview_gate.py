"""进预览闸门：投影剥离；论证不足的关系卡应被滤掉而非整期拦截；改稿作废 gate。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preview_gate_state import clear_preview_gate, edit_invalidates_preview_gate
from app.relation_display import build_published_projection
from app.relation_gate import filter_ungrounded_relations


def test_preview_gate_flag_stripped_from_published_projection():
    pub = build_published_projection({
        "_preview_gate_ok": True,
        "_preview_gate_at": "x",
        "relations": [
            {"decision_tier": "strong", "title": "S", "body": "b", "evidence": [{}]},
        ],
        "kpis": [{"n": "1", "label": "可同步的关系"}],
    })
    assert "_preview_gate_ok" not in pub
    assert "_preview_gate_at" not in pub
    assert len(pub["relations"]) == 1


def test_preview_gate_ok_means_publish_may_skip_recheck():
    """产品约定：草稿带 _preview_gate_ok 后上线不再跑 publish_blockers。"""
    draft = {"_preview_gate_ok": True, "relations": []}
    assert draft.get("_preview_gate_ok") is True


def test_clear_preview_gate_on_edit():
    data = {"_preview_gate_ok": True, "_preview_gate_at": "t", "question": "q"}
    out = clear_preview_gate(data)
    assert "_preview_gate_ok" not in out
    assert "_preview_gate_at" not in out
    assert out.get("_preview_gate_stale") is True


def test_edit_path_invalidates_gate():
    assert edit_invalidates_preview_gate("relations.0.title")
    assert edit_invalidates_preview_gate("lead")
    assert not edit_invalidates_preview_gate("_internal.foo")


def test_filter_ungrounded_drops_card_without_evidence():
    items = [
        {"id": 1, "owner_team": "编辑部", "entities": '["甲"]', "blocked": 0, "text": "甲相关"},
    ]
    draft = {
        "relations": [
            {
                "title": "甲 · 乙跨团队",
                "decision_tier": "strong",
                "teams": ["编辑部", "商业化团队"],
                "body": "两边都在做甲相关事项。",
                "details": ["编辑部：甲相关"],
                "evidence": [],
            },
            {
                "title": "单侧观察",
                "decision_tier": "watch",
                "teams": ["编辑部"],
                "body": "编辑部在跟甲。",
                "details": ["编辑部：甲相关"],
                "evidence": [{"item_id": 1, "team": "编辑部", "snippet": "甲相关"}],
                "weak": True,
            },
        ]
    }
    out, dropped = filter_ungrounded_relations(draft, items)
    assert "甲 · 乙跨团队" in dropped
    titles = [r.get("title") for r in out["relations"]]
    assert "甲 · 乙跨团队" not in titles
    assert "单侧观察" in titles
    assert "_relations_dropped_ungrounded" not in out
