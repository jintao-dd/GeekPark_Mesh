"""filter_ungrounded + duplicate Warning + body paraphrase 收口。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_gate import (
    annotate_suspected_duplicates,
    filter_ungrounded_relations,
)


def _items_oppo():
    return [
        {
            "id": 1,
            "source_id": 10,
            "owner_team": "商业化团队",
            "pointer": "a",
            "entities": '["OPPO"]',
            "text": "沟通中：蚂蚁、京东、OPPO、阿里云",
            "blocked": 0,
        },
        {
            "id": 2,
            "source_id": 11,
            "owner_team": "编辑部",
            "pointer": "b",
            "entities": '["OPPO"]',
            "text": "OPPO 商务内容：进行中，商务",
            "blocked": 0,
        },
    ]


def test_paraphrase_body_kept_not_hidden():
    """总结 body 允许 paraphrase；不应因字面不够整卡不成卡。"""
    items = _items_oppo()
    draft = {
        "relations": [
            {
                "title": "OPPO 商务内容",
                "label": "已联动",
                "decision_tier": "strong",
                "teams": ["商业化团队", "编辑部"],
                "body": "两边都在跟 OPPO 商务内容，沟通名单与选题推进尚未对齐。",
                "details": [
                    "商业化团队：沟通中含 OPPO。",
                    "编辑部：OPPO 商务内容进行中。",
                    "商业化团队：OPPO brief 已完成两次采访，初稿在催。",  # 过细，应被剥掉并回填
                ],
                "sources": ["商业化团队例会提及", "编辑部选题表"],
                "evidence": [
                    {
                        "item_id": 1,
                        "team": "商业化团队",
                        "snippet": "沟通中：蚂蚁、京东、OPPO、阿里云",
                    },
                    {
                        "item_id": 2,
                        "team": "编辑部",
                        "snippet": "OPPO 商务内容：进行中，商务",
                    },
                ],
            }
        ]
    }
    out, not_formed = filter_ungrounded_relations(draft, items)
    assert not_formed == []
    assert len(out["relations"]) == 1
    rel = out["relations"][0]
    assert "OPPO" in (rel.get("body") or "")
    assert "_relations_dropped_ungrounded" not in out
    # 过细 detail 被剥掉后仍有两侧可用 detail
    assert len(rel.get("details") or []) >= 2


def test_no_body_kept_when_details_present():
    """有 details/evidence 时空 body 仍成卡（有来源即可）。"""
    items = _items_oppo()
    draft = {
        "relations": [
            {
                "title": "OPPO",
                "decision_tier": "strong",
                "teams": ["商业化团队", "编辑部"],
                "body": "",
                "details": ["商业化团队：沟通中含 OPPO。", "编辑部：OPPO 商务内容进行中。"],
                "evidence": [
                    {"item_id": 1, "team": "商业化团队", "snippet": "沟通中：OPPO"},
                    {"item_id": 2, "team": "编辑部", "snippet": "OPPO 商务内容：进行中"},
                ],
            }
        ]
    }
    out, not_formed = filter_ungrounded_relations(draft, items)
    assert not_formed == []
    assert len(out["relations"]) == 1
    # sanitize 可能用 details 合成 body
    rel = out["relations"][0]
    assert (rel.get("body") or "").strip() or (rel.get("details") or [])


def test_suspected_duplicate_warning_keeps_both():
    rels = [
        {
            "title": "云栖大会博鳌论坛",
            "body": "同一场云栖，两队在推进。",
            "details": ["商业化团队：博鳌执行中", "编辑部：圆桌待对思路"],
            "evidence": [
                {"item_id": 10, "team": "商业化团队", "snippet": "云栖大会博鳌论坛执行中"},
                {"item_id": 11, "team": "编辑部", "snippet": "云栖圆桌待对思路"},
            ],
            "item_ids": [10, 11],
        },
        {
            "title": "云栖大会圆桌与深度稿",
            "body": "同一场云栖，两侧各知一半。",
            "details": ["编辑部：圆桌进行中", "商业化团队：博鳌已签约"],
            "evidence": [
                {"item_id": 11, "team": "编辑部", "snippet": "云栖圆桌待对思路"},
                {"item_id": 10, "team": "商业化团队", "snippet": "云栖大会博鳌论坛执行中"},
            ],
            "item_ids": [10, 11],
        },
    ]
    out = annotate_suspected_duplicates(rels)
    assert len(out) == 2
    assert all(r.get("_draft_warning") == "suspected_duplicate" for r in out)
    assert out[0].get("_dup_peer_titles")
    assert out[0].get("needs_review") is True


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
