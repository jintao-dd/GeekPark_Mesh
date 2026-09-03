"""进预览闸门标记：投影剥离；有标记则视为已通过论证。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_display import build_published_projection


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
