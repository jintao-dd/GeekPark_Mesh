"""段级 hash reuse + relation_input 指纹失效。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_segment_digest_stable_and_pointer_roundtrip():
    from app.segment_cache import (
        clone_item_for_reuse,
        digest_from_item,
        parse_segment_digest,
        segment_digest,
        stamp_segment_digest,
        strip_segment_digest_prefix,
    )

    d1 = segment_digest(stype="T3", title="A", text="hello")
    d2 = segment_digest(stype="T3", title="A", text="hello")
    d3 = segment_digest(stype="T3", title="A", text="hello!")
    assert d1 == d2 and d1 != d3

    it = stamp_segment_digest({"pointer": "面壁", "text": "x", "zone": 3}, d1)
    assert digest_from_item(it) == d1
    dig, bare = parse_segment_digest(it["pointer"])
    assert dig == d1 and bare == "面壁"
    assert strip_segment_digest_prefix(it["pointer"]) == "面壁"

    reused = clone_item_for_reuse(it, digest=d1)
    assert "id" not in reused
    assert digest_from_item(reused) == d1


def test_normalize_pointer_strips_segment_prefix():
    from app.owner_guard import normalize_pointer, provenance_key

    ptr = "§sd:abcdef0123456789ab|面壁智能—詹杨帆"
    assert normalize_pointer(ptr) == "面壁智能—詹杨帆"
    k1 = provenance_key({"pointer": ptr, "source_id": 1, "entities": []})
    k2 = provenance_key({"pointer": "面壁智能—詹杨帆", "source_id": 1, "entities": []})
    assert k1 == k2


def test_extract_source_reuses_unchanged_segment(monkeypatch):
    from app import llm

    calls = {"n": 0}

    def fake_extract_items(*args, **kwargs):
        calls["n"] += 1
        return [{
            "zone": 3, "level": "L1", "kind": "fact", "text": "新抽",
            "entities": ["X"], "roles": [], "signals": [],
            "source_label": "测", "pointer": "p1", "blocked": 0, "team": "硅谷 BD 团队",
        }]

    monkeypatch.setattr(llm, "extract_items", fake_extract_items)

    seg_a = SimpleNamespace(stype="T3", team="硅谷 BD 团队", title="段A", text="正文A", owner_hint="硅谷 BD 团队")
    seg_b = SimpleNamespace(stype="T3", team="编辑部", title="段B", text="正文B", owner_hint="编辑部")
    split = SimpleNamespace(
        segments=[seg_a, seg_b],
        mode="multi",
        warnings=[],
        boundaries=2,
        skipped=0,
        toc_count=0,
        to_meta=lambda: {"segments": 2, "mode": "multi", "confidence": "high", "warnings": []},
    )

    with mock.patch("app.aggregator.split_bundle_ex", return_value=split):
        with mock.patch("app.aggregator.should_split", return_value=True):
            from app.segment_cache import segment_digest, stamp_segment_digest

            d_a = segment_digest(stype="T3", title="段A", text="正文A")
            prior = [
                stamp_segment_digest({
                    "zone": 3, "level": "L1", "kind": "fact", "text": "旧A",
                    "entities": ["A"], "roles": [], "signals": [],
                    "source_label": "旧", "pointer": "pa", "blocked": 0, "team": "硅谷 BD 团队",
                }, d_a),
            ]
            items, meta = llm.extract_source(
                "T13", "内容中心·数据聚合", "聚合包", "x" * 100,
                channel="aggregator",
                prior_items=prior,
            )
            # 段A reuse，段B 新抽 → 只 1 次 extract_items
            assert calls["n"] == 1
            assert meta["segment_reuse"]["reused"] == 1
            assert meta["segment_reuse"]["extracted"] == 1
            texts = {it["text"] for it in items}
            assert "旧A" in texts and "新抽" in texts


def test_relation_input_fingerprint_invalidates_on_candidate_or_prompt_change(monkeypatch):
    from app import preview_progressive as prog

    cands = [{
        "candidate_id": "c1",
        "teams": ["编辑部", "硅谷 BD 团队"],
        "item_ids": [1, 2],
        "evidence": [{"item_id": 1}, {"item_id": 2}],
        "title": "面壁",
        "snippets": [{"text": "编辑部记录"}],
    }]
    cards = {"编辑部": {"bullets": ["a"]}}
    fp1 = prog.relation_input_fingerprint(candidates=cands, team_cards=cards, item_rows=[
        {"id": 1, "owner_team": "编辑部", "text": "aaa"},
        {"id": 2, "owner_team": "硅谷 BD 团队", "text": "bbb"},
    ])
    fp2 = prog.relation_input_fingerprint(candidates=cands, team_cards=cards, item_rows=[
        {"id": 1, "owner_team": "编辑部", "text": "aaa"},
        {"id": 2, "owner_team": "硅谷 BD 团队", "text": "bbb"},
    ])
    assert fp1 == fp2

    cands2 = [{**cands[0], "item_ids": [1, 2, 3]}]
    fp3 = prog.relation_input_fingerprint(candidates=cands2, team_cards=cards, item_rows=[])
    assert fp3 != fp1

    data = prog.attach_relation_input_fingerprint({"relations": [{"title": "x"}]}, fp1)
    assert prog.relation_input_fingerprint_matches(data, fp1)
    assert not prog.relation_input_fingerprint_matches(data, fp3)

    # prompt version 变了也失效
    monkeypatch.setattr(prog, "relation_prompt_version", lambda: "mutated")
    assert not prog.relation_input_fingerprint_matches(data, fp1)
