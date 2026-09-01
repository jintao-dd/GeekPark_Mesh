"""团队徽章：实线 vs → 虚线建议关注。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.owner_guard import normalize_relation_team_badges


def test_solid_and_suggested_team_badges():
    rel = {
        "title": "某公司",
        "teams": ["编辑部", "投资团队"],
        "evidence": [{"item_id": 1, "team": "编辑部", "snippet": "x"}],
    }
    out = normalize_relation_team_badges(rel)
    assert out["teams"] == ["编辑部", "→ 投资团队"]
    assert out["weak"] is True


def test_preserve_explicit_arrow_team():
    rel = {
        "title": "Armaro",
        "teams": ["Global Partnership 团队", "→ 社群 · 商业化团队"],
        "evidence": [{"item_id": 1, "team": "Global Partnership 团队", "snippet": "x"}],
    }
    out = normalize_relation_team_badges(rel)
    assert "Global Partnership" in out["teams"][0]
    assert out["teams"][1].startswith("→")
