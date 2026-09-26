"""Claim Check rule_v1：Writer 原文强度守卫 + Gold 回归。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.relation_claim_check import (
    MODE_ENFORCE,
    MODE_SHADOW,
    apply_claim_checks,
    check_relation_claim,
    claim_check_mode,
)
from eval.relation_gold_lib import BASELINE_PATH, load_gold, write_baseline


def test_mode_default_enforce(monkeypatch):
    monkeypatch.delenv("MESH_CLAIM_CHECK_MODE", raising=False)
    assert claim_check_mode() == MODE_ENFORCE


def test_title_body_details_all_checked():
    rel = {
        "title": "百度：社群与视频号联合活动已落地",
        "body": "活动排期含百度智能硬件 Workshop。",
        "details": ["周数据视频选题含百度内容方向观察"],
        "relation_type": "event_chain",
        "evidence": [
            {"item_id": 1, "team": "社群", "snippet": "活动排期含百度智能硬件 Workshop"},
            {"item_id": 2, "team": "视频号团队", "snippet": "周数据视频选题含百度内容方向观察"},
        ],
    }
    items = [
        {"id": 1, "owner_team": "社群", "text": "活动排期含百度智能硬件 Workshop"},
        {"id": 2, "owner_team": "视频号团队", "text": "周数据视频选题含百度内容方向观察"},
    ]
    out = check_relation_claim(rel, items)
    assert out["claim_verdict"] == "invalid"
    assert "title" in out["checked_span"] or out["checked_span"] == ["title"]


def test_strength_paraphrase_not_false_positive():
    """正文『达成协议』+ 证据『正式签署』→ 等级相当，不应误杀。"""
    rel = {
        "title": "面壁合作",
        "body": "已达成合作协议",
        "details": [],
        "evidence": [{"item_id": 1, "snippet": "双方正式签署合作文件"}],
    }
    items = [{"id": 1, "text": "双方正式签署合作文件", "owner_team": "编辑部"}]
    out = check_relation_claim(rel, items)
    assert out["claim_verdict"] == "valid"


def test_checks_writer_raw_not_mutated_body():
    """Claim Check 必须看 _writer_raw / 传入原文，不被后处理骨架化误导。"""
    raw_body = (
        "会议判断：豆包手机路线。三团队已就豆包形成统一商务合作推进。"
    )
    skeleton = "会议判断：豆包手机路线。"
    rel = {
        "title": "豆包：三团队统一商务合作推进",
        "body": skeleton,  # 模拟已被 lexical 弱化
        "details": [],
        "relation_type": "parallel_tracks",
        "evidence": [
            {"item_id": 1, "snippet": "豆包手机、阶跃、荣耀推出不同技术路线产品"},
        ],
        "_writer_raw": {
            "title": "豆包：三团队统一商务合作推进",
            "body": raw_body,
            "details": [],
        },
    }
    items = [{"id": 1, "text": "豆包手机、阶跃、荣耀推出不同技术路线产品"}]
    # 直接 check 弱化后的 body 可能漏掉；apply 应使用 raw
    kept, audit = apply_claim_checks([rel], items, mode=MODE_SHADOW)
    assert audit["n_invalid"] == 1
    assert kept[0]["_claim_check"]["claim_verdict"] == "invalid"
    # body 字段未被 Claim Check 改写
    assert kept[0]["body"] == skeleton


def test_a11_watch_contact_without_word_not_upgrade():
    """Helloboss 类：evidence 无「接触」字面，但 watch+有 evidence → 不因缺词判 upgrade。"""
    rel = {
        "title": "Helloboss CEO：编辑部接触",
        "body": "编辑部接触 Helloboss CEO，Helloboss 为日本 AI 求职招聘匹配应用。",
        "details": ["编辑部记录人物档案"],
        "decision_tier": "watch",
        "relation_type": "one_sided",
        "weak": True,
        "evidence": [
            {
                "item_id": 1,
                "team": "编辑部",
                "snippet": "Alex Wang（Helloboss CEO）：日本 AI 求职招聘匹配应用",
            }
        ],
    }
    items = [
        {
            "id": 1,
            "owner_team": "编辑部",
            "text": "Alex Wang（Helloboss CEO）：日本 AI 求职招聘匹配应用",
        }
    ]
    assert check_relation_claim(rel, items)["claim_verdict"] == "valid"


def test_a11_watch_still_blocks_deal_overclaim():
    """watch + 关注证据 + body 已达成合作 → 仍 invalid。"""
    rel = {
        "title": "已达成合作",
        "body": "编辑部与对方已达成合作协议。",
        "details": [],
        "decision_tier": "watch",
        "relation_type": "one_sided",
        "weak": True,
        "evidence": [{"item_id": 1, "snippet": "编辑部关注该公司产品进展"}],
    }
    items = [{"id": 1, "text": "编辑部关注该公司产品进展", "owner_team": "编辑部"}]
    out = check_relation_claim(rel, items)
    assert out["claim_verdict"] == "invalid"


def test_a11_plan_not_crushed_by_sibling_blocker():
    """Evidence A 计划 + Evidence B 尚无反馈 → 「计划参加」仍 valid。"""
    rel = {
        "title": "秒动科技：两侧联系",
        "body": "联系时尚无反馈。编辑部同事计划两周后参加视频号选题会。",
        "details": [
            "联系杨硕时，编辑部也通过 PR 联系同一人，尚无反馈",
            "编辑部同事计划两周后参加视频号选题会，探讨海外信源",
        ],
        "decision_tier": "strong",
        "relation_type": "event_chain",
        "weak": True,
        "evidence": [
            {
                "item_id": 1,
                "team": "Global Partnership 团队",
                "snippet": "联系杨硕时，编辑部也通过 PR 联系同一人，尚无反馈",
            },
            {
                "item_id": 2,
                "team": "视频号团队",
                "snippet": "编辑部同事计划两周后参加视频号选题会，探讨海外信源",
            },
        ],
    }
    items = [
        {"id": 1, "owner_team": "Global Partnership 团队", "text": "联系杨硕时尚无反馈"},
        {
            "id": 2,
            "owner_team": "视频号团队",
            "text": "编辑部同事计划两周后参加视频号选题会，探讨海外信源",
        },
    ]
    assert check_relation_claim(rel, items)["claim_verdict"] == "valid"


def test_a11_plan_to_deal_still_blocked():
    """计划证据 + 已完成合作论断 → invalid。"""
    rel = {
        "title": "已完成合作",
        "body": "双方已完成联合合作推进。",
        "details": [],
        "evidence": [
            {"item_id": 1, "snippet": "计划两周后参加选题会，尚无反馈"},
        ],
    }
    items = [{"id": 1, "text": "计划两周后参加选题会，尚无反馈"}]
    out = check_relation_claim(rel, items)
    assert out["claim_verdict"] == "invalid"


def test_a12_parallel_contact_jianlian_not_upgrade():
    """xAI 类：parallel + body「建联」+ evidence 无同词 → valid（A1.2）。"""
    rel = {
        "title": "xAI：编辑部技术成员建联与视频号财报选题",
        "body": "编辑部有 xAI 技术成员建联，视频号做 xAI 财报选题，同一公司不同触点。",
        "details": ["视频号团队：周数据含 SpaceX/xAI 合并财报选题"],
        "decision_tier": "parallel",
        "relation_type": "parallel_tracks",
        "evidence": [
            {
                "item_id": 1,
                "team": "编辑部",
                "snippet": "Shuyang Gao（xAI 技术成员）：xAI 为 Elon Musk 创立的 AI 公司",
            },
            {
                "item_id": 2,
                "team": "视频号团队",
                "snippet": "多条视频聚焦马斯克身家与 SpaceX/xAI 合并财报",
            },
        ],
    }
    items = [
        {"id": 1, "owner_team": "编辑部", "text": "Shuyang Gao（xAI 技术成员）"},
        {"id": 2, "owner_team": "视频号团队", "text": "SpaceX/xAI 合并财报选题"},
    ]
    assert check_relation_claim(rel, items)["claim_verdict"] == "valid"


def test_a12_parallel_deal_still_invalid():
    """parallel + 统一合作推进 → 仍 invalid（不因 A1.2 放宽）。"""
    rel = {
        "title": "豆包：三团队统一商务合作推进",
        "body": "三团队已就豆包形成统一商务合作推进。",
        "details": [],
        "relation_type": "parallel_tracks",
        "evidence": [
            {"item_id": 1, "snippet": "会议判断：豆包手机行业路线"},
            {"item_id": 2, "snippet": "多条视频聚焦豆包交易入口"},
        ],
    }
    items = [
        {"id": 1, "text": "会议判断：豆包手机行业路线"},
        {"id": 2, "text": "多条视频聚焦豆包交易入口"},
    ]
    assert check_relation_claim(rel, items)["claim_verdict"] == "invalid"


def test_shadow_keeps_invalid_enforce_drops():
    rel = {
        "title": "已落地联合活动",
        "body": "社群与视频号已就百度联合活动达成合作并完成落地。",
        "details": [],
        "evidence": [{"item_id": 1, "snippet": "活动排期含百度 Workshop"}],
    }
    items = [{"id": 1, "text": "活动排期含百度 Workshop"}]
    kept_s, a_s = apply_claim_checks([rel], items, mode=MODE_SHADOW)
    assert len(kept_s) == 1
    assert a_s["n_invalid"] == 1
    assert a_s["n_dropped_enforce"] == 0
    kept_e, a_e = apply_claim_checks([rel], items, mode=MODE_ENFORCE)
    assert kept_e == []
    assert a_e["n_dropped_enforce"] == 1


def test_gold_claim_cases_rule_v1():
    cases = [c for c in load_gold() if c["expect"] in ("claim_valid", "claim_invalid")]
    assert len(cases) >= 8
    fails = []
    for c in cases:
        out = check_relation_claim(c["rel"], c["items"])
        want = "valid" if c["expect"] == "claim_valid" else "invalid"
        if out["claim_verdict"] != want:
            fails.append((c["id"], want, out["claim_verdict"], out.get("claim_reason")))
    assert fails == [], fails


def test_baseline_includes_claim_check():
    from eval.relation_gold_lib import build_baseline

    payload = build_baseline()
    assert payload["n_claim_cases"] >= 6
    for row in payload["cases"]:
        assert "current_claim_check" in row
        assert row["current_claim_check"]["claim_verdict"] in (
            "valid",
            "invalid",
            "uncertain",
        )
        # 禁止把 lexical 当成 claim
        assert "claim_valid" not in row.get("current_claim_check", {})
    path = write_baseline()
    assert path == BASELINE_PATH
    disk = json.loads(path.read_text(encoding="utf-8"))
    # Gold invalid 漏网（lexical 过）应被 claim check 打掉
    caught = [
        r
        for r in disk["cases"]
        if r["gold_claim_invalid"]
        and r.get("current_line_grounded")
        and r["current_claim_check"]["claim_verdict"] == "invalid"
    ]
    assert len(caught) >= 3, caught
