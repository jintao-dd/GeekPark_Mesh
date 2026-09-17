# -*- coding: utf-8 -*-
"""Mere-mention vs real engagement gate."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_decision import apply_evidence_gate
from app.relation_decision_audit import GATE_CODE_MERE_MENTION
from app.relation_gate import evidence_has_real_engagement, relation_fails_grounding


def test_engagement_detector_real_vs_mere():
    assert evidence_has_real_engagement(
        [{"snippet": "与 Reverie AI 联合创始人开过沟通会，已约下一步"}],
        title="Reverie AI",
    )
    assert evidence_has_real_engagement(
        [{"snippet": "京东合同流程中，合作稿正在催初稿"}],
        title="京东合作稿",
    )
    assert evidence_has_real_engagement(
        [{"snippet": "编辑部实测摩斯多说话人转写"}],
        title="摩斯转写",
    )
    assert not evidence_has_real_engagement(
        [{"snippet": "把 Notta.ai 创始人张岩列为前沿社潜在会员"}],
        title="Notta.ai 张岩",
    )
    assert not evidence_has_real_engagement(
        [{"snippet": "陈宇森任 Alibaba Cloud LATAM 总经理，驻浙江"}],
        title="陈宇森",
    )
    assert not evidence_has_real_engagement(
        [{"snippet": "Shuyang Gao 为 xAI 的 Coding & Reasoning 技术成员"}],
        title="xAI",
    )
    # 建联 + 已聊 → 算真实互动
    assert evidence_has_real_engagement(
        [{"snippet": "查晟经 Lilyann 建联，已与张鹏视频会议聊过"}],
        title="查晟",
    )


def test_mere_mention_skipped_at_gate():
    cand = {
        "candidate_id": "c_mere",
        "title": "Arvin Sun",
        "teams": ["硅谷 BD 团队", "→ 投资团队"],
        "weak": True,
        "candidate_kind": "routing",
        "routing_targets": ["投资团队"],
        "team_facts": [
            {
                "team": "硅谷 BD 团队",
                "item_ids": [10],
                "snippets": ["孙邻家在 SunBoy Venture Fund 做投资"],
                "sources": ["硅谷周报"],
            }
        ],
    }
    decisions = [
        {
            "candidate_id": "c_mere",
            "decision": "keep",
            "label": "海外新发现，国内尚未接触",
            "relation_type": "overseas_link",
            "decision_tier": "watch",
            "reason": "投资人线索",
            "evidence_refs": [10],
        }
    ]
    items = [
        {
            "id": 10,
            "source_id": 1,
            "owner_team": "硅谷 BD 团队",
            "pointer": "a",
            "entities": '["Arvin Sun"]',
            "text": "孙邻家在 SunBoy Venture Fund 做投资",
            "source_label": "硅谷周报",
            "blocked": 0,
        }
    ]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    assert approved == []
    assert audit["rows"][0]["gate_decision"] == "skip"
    assert audit["rows"][0]["gate_reason_code"] == GATE_CODE_MERE_MENTION


def test_real_contact_routing_still_kept():
    cand = {
        "candidate_id": "c_real",
        "title": "Mentiforce",
        "teams": ["硅谷 BD 团队", "→ 投资团队"],
        "weak": True,
        "candidate_kind": "routing",
        "routing_targets": ["投资团队"],
        "team_facts": [
            {
                "team": "硅谷 BD 团队",
                "item_ids": [11],
                "snippets": ["与 Mentiforce CEO 开过 Zoom 沟通会，约定长期互相引荐"],
                "sources": ["硅谷周报"],
            }
        ],
    }
    decisions = [
        {
            "candidate_id": "c_real",
            "decision": "keep",
            "label": "海外接触，国内可能承接",
            "relation_type": "overseas_link",
            "decision_tier": "watch",
            "reason": "已沟通可路由",
            "evidence_refs": [11],
        }
    ]
    items = [
        {
            "id": 11,
            "source_id": 1,
            "owner_team": "硅谷 BD 团队",
            "pointer": "a",
            "entities": '["Mentiforce"]',
            "text": "与 Mentiforce CEO 开过 Zoom 沟通会，约定长期互相引荐",
            "source_label": "硅谷周报",
            "blocked": 0,
        }
    ]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    assert len(approved) == 1
    assert audit["rows"][0]["gate_decision"] == "keep"


def test_grounding_also_rejects_mere_mention():
    rel = {
        "title": "xAI 技术成员",
        "body": "Shuyang Gao 为 xAI 技术成员。",
        "details": ["硅谷 BD 团队：Shuyang Gao 为 xAI 的 Coding & Reasoning 技术成员"],
        "evidence": [
            {
                "team": "硅谷 BD 团队",
                "snippet": "Shuyang Gao 为 xAI 的 Coding & Reasoning 技术成员",
                "item_id": 1,
            }
        ],
        "weak": True,
        "decision_tier": "watch",
    }
    errs = relation_fails_grounding(rel, [])
    assert any("点名" in e or "沟通" in e for e in errs)
