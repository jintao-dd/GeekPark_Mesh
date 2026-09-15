"""客户阶段名单拆分 + ⑤区安全不降级。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.item_list_split import expand_stage_list_items, maybe_split_stage_list_item
from app.zone_hard import apply_hard_blocks, hard_block_reason


def _base(**kwargs):
    row = {
        "zone": 3,
        "level": "L1",
        "kind": "fact",
        "text": "",
        "entities": [],
        "roles": ["客户"],
        "signals": ["合作"],
        "source_label": "商业化团队例会提及",
        "pointer": "t1",
        "blocked": 0,
        "team": "商业化团队",
        "owner_team": "商业化团队",
    }
    row.update(kwargs)
    return row


def test_split_multi_client_stage_list():
    it = _base(
        text="沟通中：蚂蚁、京东、OPPO、阿里云",
        entities=["蚂蚁", "京东", "OPPO", "阿里云"],
    )
    kids = maybe_split_stage_list_item(it)
    assert len(kids) == 4
    texts = {k["text"] for k in kids}
    assert "OPPO：沟通中" in texts
    assert "蚂蚁：沟通中" in texts
    assert all("stage_list_split" in (k.get("signals") or []) for k in kids)
    assert all(len(k["entities"]) == 1 for k in kids)


def test_split_preserves_paren_notes():
    it = _base(text="已签约执行中：比亚迪（30 秒简讯视频）、AWS（按 PO 推进）")
    kids = maybe_split_stage_list_item(it)
    assert len(kids) == 2
    by_text = {k["text"]: k for k in kids}
    assert "比亚迪：已签约执行中，30 秒简讯视频" in by_text
    assert "AWS：已签约执行中，按 PO 推进" in by_text


def test_single_subject_not_split():
    it = _base(text="OPPO：沟通中，商务侧推进品牌合作意向", entities=["OPPO"])
    assert maybe_split_stage_list_item(it) == [it]


def test_thin_single_after_stage_colon_one_name_not_split():
    it = _base(text="沟通中：OPPO", entities=["OPPO"])
    assert maybe_split_stage_list_item(it) == [it]


def test_blocked_parent_not_split():
    it = _base(
        text="沟通中：蚂蚁、京东",
        zone=5,
        level="L3",
        blocked=1,
    )
    assert maybe_split_stage_list_item(it) == [it]


def test_zone5_patterns_still_block_after_split():
    """拆分不得洗白含金额等⑤区命中句。"""
    assert hard_block_reason("估值 3 亿美元") is not None
    money = apply_hard_blocks(
        [
            _base(text="甲公司：沟通中，估值 3 亿美元", entities=["甲公司"]),
        ]
    )
    assert money[0]["blocked"] == 1

    # 名单拆开后，带金额说明的子条仍应被硬拦
    kids = expand_stage_list_items(
        [
            _base(text="沟通中：甲公司（估值 3 亿美元）、乙公司"),
        ]
    )
    assert len(kids) == 2
    blocked = apply_hard_blocks(kids)
    by_name = {k["entities"][0]: k for k in blocked}
    assert by_name["甲公司"]["blocked"] == 1
    assert by_name["乙公司"]["blocked"] == 0


def test_expand_then_hard_block_keeps_clean_children():
    raw = [
        _base(
            text="接触中：志维、MiniMax",
            entities=["志维", "MiniMax"],
        ),
        _base(
            text="对人评价：某同事很差",
            zone=5,
            level="L3",
            blocked=1,
            entities=[],
        ),
    ]
    out = apply_hard_blocks(expand_stage_list_items(raw))
    live = [x for x in out if not int(x.get("blocked") or 0)]
    assert len(live) == 2
    assert {x["text"] for x in live} == {"志维：接触中", "MiniMax：接触中"}
    blocked = [x for x in out if int(x.get("blocked") or 0)]
    assert len(blocked) == 1
    assert blocked[0]["zone"] == 5


def test_zone5_rule_text_unchanged_in_base_rules():
    """回归：不得改动⑤区安全边界措辞。"""
    p = Path(__file__).resolve().parents[1] / "app" / "prompts" / "00_base_rules.md"
    text = p.read_text(encoding="utf-8")
    assert "⑤ 人与钱：对任何人的评价（含老板、同事、外部人员、供应商）、会员催款、LP 认购、个人事务、薪酬/编制/预算焦虑、交易费点、项目去留的内部表态、情绪句 → **L3，标 zone=5，永不进分发**" in text


def test_zone_hard_patterns_untouched_smoke():
    assert hard_block_reason("谈判底线已定") is not None
    assert hard_block_reason("年薪 50 万") is not None
    assert hard_block_reason("OPPO：沟通中") is None
