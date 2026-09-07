"""渐进式预览契约单测。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preview_progressive import (
    allows_preview_entry,
    attach_card_fingerprint,
    build_skeleton_draft,
    card_fingerprint,
    finalize_preview_flags,
    is_building,
    items_fingerprint,
    mark_phase,
)
from app.relation_display import build_published_projection
from app.db import issue_json_renderable


def test_skeleton_is_renderable_and_allows_entry():
    sk = build_skeleton_draft(
        slug="2026-9-1", period_label="9.1", version="v1", teams=["编辑部", "视频号团队"]
    )
    assert issue_json_renderable(sk)
    assert allows_preview_entry(sk)
    assert is_building(sk)
    assert not sk.get("_preview_gate_ok")
    pub = build_published_projection(sk)
    assert "_preview_building" not in pub
    assert "_preview_partial_ready" not in pub


def test_finalize_clears_building_sets_gate():
    sk = build_skeleton_draft(slug="x", period_label="p", version=1, teams=[])
    out = finalize_preview_flags(sk, stamp="t")
    assert out.get("_preview_gate_ok") is True
    assert not is_building(out)
    assert allows_preview_entry(out)


def test_mark_phase_keeps_partial_clears_gate():
    data = {"_preview_gate_ok": True, "question": "q"}
    out = mark_phase(data, "cards", cards_done=["编辑部"])
    assert out.get("_preview_building")
    assert "_preview_gate_ok" not in out
    assert out["_preview_cards_done"] == ["编辑部"]


def test_items_fingerprint_stable_and_reuse():
    items = [{"text": "甲", "entities": '["甲"]', "zone": "①", "kind": "fact"}]
    fp = items_fingerprint(items)
    assert fp == items_fingerprint(list(items))
    card = attach_card_fingerprint({"title": "卡"}, fp)
    assert card_fingerprint(card) == fp
    assert items_fingerprint([{"text": "乙", "entities": "", "zone": "", "kind": ""}]) != fp


def test_stale_still_allows_entry():
    assert allows_preview_entry({"_preview_gate_stale": True})
    assert not allows_preview_entry({"question": "only"})
