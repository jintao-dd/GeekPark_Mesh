"""跨期关系指纹单测。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_continue import (
    annotate_candidates_with_continuity,
    normalize_relation_title,
    relation_fingerprint,
)


def test_fingerprint_ignores_team_order_and_punct():
    a = relation_fingerprint("面壁智能 · 合作", ["编辑部", "硅谷 BD 团队"])
    b = relation_fingerprint("面壁智能·合作", ["硅谷 BD 团队", "编辑部"])
    assert a == b


def test_annotate_continued_from():
    prior = {
        relation_fingerprint("甲乙联动", ["编辑部", "商业化团队"]): {
            "slug": "2026-8-17",
            "title": "甲乙联动",
            "teams": ["编辑部", "商业化团队"],
        }
    }
    cands = [{"candidate_title": "甲乙联动", "teams": ["商业化团队", "编辑部"]}]
    annotate_candidates_with_continuity(cands, prior)
    assert cands[0].get("continued_from", {}).get("slug") == "2026-8-17"
    assert cands[0].get("relation_fp")


def test_normalize_strips_punct():
    assert normalize_relation_title("A · B") == normalize_relation_title("A·B")
