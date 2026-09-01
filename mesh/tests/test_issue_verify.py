"""Phase 3 issue_verify 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.issue_verify import (
    build_draft_evidence_corpus,
    collect_unsupported_flags,
    verify_issue_draft,
)
from app.relation_gate import issue_publish_blockers


def _items():
    return [
        {
            "id": 1,
            "source_id": 10,
            "owner_team": "编辑部",
            "pointer": "面壁—詹",
            "entities": '["詹杨帆","面壁智能"]',
            "text": "编辑部与詹杨帆谈端云协同、路由模型、事故处理 Agent。",
            "source_label": "编辑部沟通记录",
            "blocked": 0,
        },
    ]


def test_keywords_fake_team_row_trimmed():
    draft = {
        "lead": "本期由编辑部的记录汇成。",
        "relations": [],
        "keywords": {
            "groups": [{
                "title": "关注的公司与人",
                "items": [{
                    "name": "面壁智能 · 詹杨帆",
                    "teams": ["编辑部", "硅谷 BD 团队"],
                    "rows": [
                        {"k": "编辑部", "v": "端云协同、路由模型一手对话"},
                        {"k": "硅谷 BD 团队", "v": "终端是 AI 进入物理世界的路径判断"},
                    ],
                }],
            }],
        },
    }
    out = verify_issue_draft(draft, _items())
    rows = out["keywords"]["groups"][0]["items"][0]["rows"]
    keys = [r["k"] for r in rows]
    assert "编辑部" in keys
    assert "硅谷 BD 团队" not in keys
    assert out["_verify"]["keywords_rows_trimmed"] >= 1


def test_lead_unsupported_trimmed():
    draft = {
        "lead": "本期首次出现完全编造的公司 XYZ。编辑部与詹杨帆谈端云协同。",
        "relations": [],
        "keywords": {"groups": []},
    }
    out = verify_issue_draft(draft, _items())
    assert "XYZ" not in out["lead"]
    assert "詹杨帆" in out["lead"] or "编辑部" in out["lead"]


def test_publish_blocker_no_evidence_strong_relation():
    items = _items()
    draft = {
        "relations": [{
            "title": "面壁智能 · 詹杨帆",
            "weak": False,
            "teams": ["编辑部"],
            "body": "编辑部有记录。",
            "details": [],
            "evidence": [],
        }],
    }
    errs = issue_publish_blockers(draft, items)
    assert any("evidence" in e for e in errs)


def test_corpus_includes_item_text():
    corpus, entries = build_draft_evidence_corpus(_items())
    assert "端云协同" in corpus
    assert entries[0]["item_id"] == 1


def test_unsupported_flags_after_verify_clean_relation():
    rel = {
        "title": "面壁智能 · 詹杨帆",
        "weak": True,
        "teams": ["编辑部"],
        "body": "编辑部本期均有与「面壁智能 · 詹杨帆」相关的记录。",
        "details": ["编辑部记录：与詹杨帆谈端云协同、路由模型、事故处理 Agent。"],
        "evidence": [{
            "item_id": 1,
            "source_id": 10,
            "team": "编辑部",
            "snippet": "编辑部与詹杨帆谈端云协同、路由模型、事故处理 Agent。",
        }],
    }
    draft = verify_issue_draft({"relations": [rel], "lead": ""}, _items())
    flags = [f for f in collect_unsupported_flags(draft) if "detail unsupported" in f or "body 无法" in f]
    assert not flags
