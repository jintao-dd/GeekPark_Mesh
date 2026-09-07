"""关系 Gold 烟测：结构 + 核心 gate/指纹约定。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.owner_guard import relation_team_supported
from app.relation_continue import relation_fingerprint
from app.relation_gate import filter_ungrounded_relations, relation_fails_grounding

GOLD = Path(__file__).resolve().parents[1] / "eval" / "relation_gold_v1.jsonl"


def _cases():
    rows = []
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_gold_file_loads():
    cases = _cases()
    assert len(cases) >= 5
    ids = {c["id"] for c in cases}
    assert "g01" in ids and "g02" in ids


def test_gold_keep_and_drop():
    by_id = {c["id"]: c for c in _cases()}
    g1 = by_id["g01"]
    assert relation_team_supported(g1["items"], g1["rel"], "编辑部")
    assert relation_team_supported(g1["items"], g1["rel"], "硅谷 BD 团队")
    g2 = by_id["g02"]
    draft = {"relations": [g2["rel"]]}
    out, dropped = filter_ungrounded_relations(draft, g2["items"])
    assert g2["rel"]["title"] in dropped
    assert out["relations"] == []
    g3 = by_id["g03"]
    assert not relation_fails_grounding(g3["rel"], g3["items"])


def test_gold_team_via_evidence():
    g4 = next(c for c in _cases() if c["id"] == "g04")
    assert relation_team_supported(g4["items"], g4["rel"], "编辑部")
    assert relation_team_supported(g4["items"], g4["rel"], "商业化团队")


def test_gold_fingerprint_stable():
    g5 = next(c for c in _cases() if c["id"] == "g05")
    a = relation_fingerprint(g5["title_a"], g5["teams_a"])
    b = relation_fingerprint(g5["title_b"], g5["teams_b"])
    assert a == b
