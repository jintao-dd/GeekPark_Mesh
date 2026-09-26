"""Relation Writing Module 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.edm import FLAG_COLORS
from app.relation_decision_consistency import normalize_relation_label
from app.relation_writer import (
    LABEL_WRITE_HINTS,
    LOCKED_FIELDS,
    body_is_template,
    body_restates_details,
    dedupe_near_details,
    enforce_narrative_hygiene,
    label_write_hint,
    merge_writing,
    relation_object_from_gate,
    strip_route_meta_copy,
    title_from_details,
    title_has_label_leak,
    title_is_formula,
    to_writer_input,
    write_relations,
)


def _sample_object():
    return relation_object_from_gate({
        "candidate_id": "c9",
        "candidate_title": "豆包 · 字节跳动",
        "label": "同一赛道，各自在做",
        "relation_type": "parallel_tracks",
        "decision_tier": "parallel",
        "teams": ["商业化团队", "视频号团队"],
        "sources": ["商业化周报", "视频号周数据"],
        "evidence": [
            {"item_id": 123, "team": "商业化团队", "snippet": "豆包 AI 手机判断", "source_label": "商业化周报"},
            {"item_id": 456, "team": "视频号团队", "snippet": "豆包收费视频", "source_label": "视频号周数据"},
        ],
        "item_ids": [123, 456],
        "weak": False,
        "provenance_ok": True,
        "decision_reason": "两团队分别围绕豆包开展动作",
    })


def test_writer_input_excludes_locked_mutation_surface():
    obj = _sample_object()
    inp = to_writer_input(obj)
    assert inp["candidate_id"] == "c9"
    assert inp["label"] == "同一赛道，各自在做"
    assert "分别写两队各自已发生动作" in inp["label_hint"]
    assert "title 禁止标签词" in inp["label_hint"] or "禁止写标签" in inp["label_hint"]
    assert "A × B" in inp["label_hint"] or "对照表" in inp["label_hint"]
    assert "复述" in inp["label_hint"] or "增量" in inp["label_hint"]
    assert len(inp["evidence"]) == 2
    assert inp["relation_reason"] == "两团队分别围绕豆包开展动作"
    assert "sources" not in inp


def test_label_write_hints_cover_canonical_labels():
    """规范 17 标签均有 hint；别名归一后也能取到。"""
    canonical = {normalize_relation_label(lb) for lb in FLAG_COLORS}
    missing = [lb for lb in canonical if lb not in LABEL_WRITE_HINTS]
    assert not missing, f"missing LABEL_WRITE_HINTS: {missing}"
    hint_parallel = label_write_hint("同一条赛道，各自在做")
    assert hint_parallel.startswith(LABEL_WRITE_HINTS["同一赛道，各自在做"])
    assert "对照表" in hint_parallel or "A × B" in hint_parallel
    assert "不根据缺失证据推断" in label_write_hint("海外新发现，国内尚未接触")
    assert "不补充接触推断" in label_write_hint("一方有需求，另一方尚未接触")


def test_flag_colors_unique_per_canonical_label():
    """规范标签色互异（别名可与主标签同色）。"""
    by_color: dict[str, list[str]] = {}
    for lab, color in FLAG_COLORS.items():
        canon = normalize_relation_label(lab)
        by_color.setdefault(color, [])
        if canon not in by_color[color]:
            by_color[color].append(canon)
    collisions = {c: labs for c, labs in by_color.items() if len(labs) > 1}
    assert not collisions, f"shared flag colors: {collisions}"


def test_merge_writing_only_adds_narrative_fields():
    obj = _sample_object()
    writing = {
        "candidate_id": "c9",
        "title": "豆包（字节跳动）：两条通道各自在跟",
        "body": "商业化团队关注豆包终端价值，视频号团队产出豆包相关视频。",
        "details": [
            "商业化团队：豆包 AI 手机判断",
            "视频号团队：豆包收费视频",
        ],
        "label": "已联动",
        "teams": ["假团队"],
        "evidence": [],
    }
    rel = merge_writing(obj, writing)
    assert rel["title"] == writing["title"]
    assert rel["body"] == writing["body"]
    assert rel["label"] == "同一赛道，各自在做"
    assert rel["teams"] == ["商业化团队", "视频号团队"]
    assert len(rel["evidence"]) == 2
    for f in ("sources", "weak", "provenance_ok", "item_ids"):
        assert rel.get(f) == obj.get(f)
    assert rel["decision_tier"] == "parallel"
    assert rel["relation_type"] == "parallel_tracks"


def test_relation_object_from_gate_carries_decision_tier():
    obj = relation_object_from_gate({
        "candidate_id": "c1",
        "label": "已联动",
        "relation_type": "event_chain",
        "decision_tier": "strong",
        "teams": ["编辑部"],
        "evidence": [{"item_id": 1, "team": "编辑部", "snippet": "x"}],
    })
    assert obj["decision_tier"] == "strong"
    assert obj["relation_type"] == "event_chain"


def test_merge_writing_preserves_decision_tier_when_writer_omits():
    obj = _sample_object()
    writing = {
        "candidate_id": "c9",
        "title": "豆包两条线",
        "body": "各自推进。",
        "details": [],
        "decision_tier": "strong",
        "relation_type": "event_chain",
    }
    rel = merge_writing(obj, writing)
    assert rel["decision_tier"] == "parallel"
    assert rel["relation_type"] == "parallel_tracks"


def test_write_relations_allows_empty_body_with_details(monkeypatch):
    from app import relation_writer as rw

    monkeypatch.setattr(rw, "call_writer_field_rewrite", lambda tasks: {})
    obj = _sample_object()
    rels, skipped = write_relations(
        [obj],
        writings=[{
            "candidate_id": "c9",
            "title": "豆包两条线",
            "body": "",
            "details": ["商业化团队：豆包 AI 手机判断", "视频号团队：豆包收费视频"],
        }],
    )
    assert skipped == []
    assert len(rels) == 1
    # 空 body + details → 成卡；不再截字合成 body
    assert not (rels[0].get("body") or "").strip()
    assert not rels[0].get("_body_synthesized_from_details")
    assert "另一侧" not in (rels[0].get("body") or "")
    assert len(rels[0]["details"]) == 2


def test_write_relations_skips_missing_title_and_narrative():
    # evidence 为空且无 title/body/details → skip（normalize 也补不出 details）
    bare = relation_object_from_gate({
        "candidate_id": "c0",
        "candidate_title": "",
        "label": "已联动",
        "relation_type": "event_chain",
        "decision_tier": "strong",
        "teams": ["编辑部"],
        "evidence": [],
        "item_ids": [],
    })
    rels, skipped = write_relations(
        [bare],
        writings=[{"candidate_id": "c0", "title": "", "body": "", "details": []}],
    )
    assert rels == []
    assert skipped[0]["reason"] == "missing_narrative_title_or_body"


def test_locked_fields_constant():
    assert "label" in LOCKED_FIELDS
    assert "decision_tier" in LOCKED_FIELDS
    assert "relation_type" in LOCKED_FIELDS
    assert "evidence" in LOCKED_FIELDS
    assert "title" not in LOCKED_FIELDS


def test_strip_route_meta_copy_investment_tails():
    assert strip_route_meta_copy(
        "张岩出现在 Global Partnership 团队 AGI Playground 前沿社活动嘉宾名单中，投资团队可承接。"
    ) == "张岩出现在 Global Partnership 团队 AGI Playground 前沿社活动嘉宾名单中。"
    assert strip_route_meta_copy(
        "Global Partnership 团队记录：新发现嘉宾：苏昊；对编辑部选题、投资团队可用"
    ) == "Global Partnership 团队记录：新发现嘉宾：苏昊"
    assert strip_route_meta_copy(
        "双方约定长期互相对接；国内编辑部选题可用得上。"
    ) == "双方约定长期互相对接"
    assert strip_route_meta_copy("赵越（仙工智能）关注中，投资团队用得上") == "赵越（仙工智能）关注中"
    # 分号后队名+用得上（线上常见漏网）
    assert strip_route_meta_copy(
        "张鹏认为应从更宏观视角看待这波 agent 创业者，记者需呈现多元观点；编辑部用得上"
    ) == "张鹏认为应从更宏观视角看待这波 agent 创业者，记者需呈现多元观点"
    assert "用得上" not in strip_route_meta_copy(
        "选题线索提到高德地图将参加发布会；编辑部用得上"
    )
    # 不要吞掉分号前的事实动作
    assert "拟联系" in strip_route_meta_copy(
        "有意加入前沿社，先从邀请参加活动开始接触（拟联系）；对编辑部、投资团队可用"
    )
    assert "拟尽量取得联系" in strip_route_meta_copy(
        "尚未接触，拟尽量取得联系；对编辑部、投资团队可用"
    )
    # 不要误伤正常「可用」事实句（无用得上类路由词）
    assert "数据可用" in strip_route_meta_copy("本期报表数据可用")


def test_title_label_leak_and_rebuild():
    assert title_has_label_leak("京东合作：两侧各知一半")
    assert title_is_formula("京东：合同催初稿 × 商务选题已提报")
    assert not title_is_formula("京东合作稿卡在催初稿")
    title, body, details, flags = enforce_narrative_hygiene(
        title="京东合作：稿件催初稿与商务选题两侧各知一半",
        body="商务选题已提报，合作稿却仍卡在催初稿，两边进度未对齐。",
        details=[
            "商业化团队：京东合同流程中，催初稿",
            "编辑部：京东商务选题进行中，提报 9/10",
        ],
        candidate_title="京东",
    )
    assert flags["title_rebuilt"]
    assert not title_has_label_leak(title)
    assert not title_is_formula(title)
    assert "催初稿" in title or "合同" in title
    assert "各知一半" not in title
    assert "×" not in title
    assert "未对齐" in body or "卡在" in body


def test_body_template_cleared():
    assert body_is_template(
        "京东这一合作，商业化团队与编辑部各掌握一部分：一边在推进合同，一边在报选题。"
    )
    title, body, details, flags = enforce_narrative_hygiene(
        title="京东：催初稿 × 选题提报",
        body="京东这一合作，商业化团队与编辑部各掌握一部分：一边在推进合同，一边在报选题。",
        details=["商业化团队：催初稿", "编辑部：选题提报"],
        candidate_title="京东",
    )
    assert flags["body_cleared_template"]
    assert flags["title_rebuilt"]
    # 套话清掉后留空；不截字合成
    assert not flags.get("body_synthesized_from_details")
    assert body == ""
    assert "×" not in title
    assert "催初稿" in title


def test_body_restates_kept_and_empty_stays_empty():
    details = [
        "商业化团队：京东合同流程中，催初稿",
        "编辑部：京东商务选题进行中，提报 9/10",
    ]
    # 概括两侧不再被硬清
    title, body, out_details, flags = enforce_narrative_hygiene(
        title="京东合作稿卡在催初稿",
        body="商业化在催初稿；编辑部京东商务选题 9/10 已提报。",
        details=details,
        candidate_title="京东",
    )
    assert body
    assert not flags.get("body_cleared_restates")
    # 空 body + details → 保持空，留给字段重写
    title2, body2, _, flags2 = enforce_narrative_hygiene(
        title="京东合作稿卡在催初稿",
        body="",
        details=details,
        candidate_title="京东",
    )
    assert not flags2.get("body_synthesized_from_details")
    assert body2 == ""


def test_dedupe_near_details():
    kept = dedupe_near_details(
        [
            "商业化团队：博联智能面向 B 端做行业模型，国内团队可对接的创业线索（会议判断，非核实事实）",
            "商业化团队：博联智能做面向 B 端的行业模型，国内团队可对接的创业线索（会议判断，非核实事实）",
            "编辑部：博联智能 · 钟琳艳（PR总监）：已接触",
        ]
    )
    assert len(kept) == 2


def test_merge_writing_strips_label_from_title():
    obj = _sample_object()
    writing = {
        "candidate_id": "c9",
        "title": "豆包 · 两队各知一半",
        "body": "商业化关注豆包终端，视频号已有相关视频，两边口径尚未对齐。",
        "details": [
            "商业化团队：豆包 AI 手机判断",
            "视频号团队：豆包收费视频",
        ],
    }
    rel = merge_writing(obj, writing)
    assert "各知一半" not in (rel.get("title") or "")
    assert "×" not in (rel.get("title") or "")
    assert rel.get("_title_rebuilt_from_details")
    assert "豆包" in (rel.get("title") or "") or "AI" in (rel.get("title") or "")


def test_narrative_violation_codes_and_field_rewrite(monkeypatch):
    from app import relation_writer as rw

    codes = rw.narrative_violation_codes(
        "京东：催初稿 × 选题提报",
        "",
        ["商业化团队：催初稿", "编辑部：选题提报"],
    )
    assert "title_formula" in codes
    assert "body_empty" in codes

    calls = {"n": 0}

    def fake_rewrite(tasks):
        calls["n"] += 1
        assert tasks and "title" in tasks[0]["fix_fields"]
        return {
            tasks[0]["candidate_id"]: {
                "title": "京东合作稿卡在催初稿",
                "body": "商务选题已提报，合作稿却仍卡在催初稿，两边进度未对齐。",
            }
        }

    monkeypatch.setattr(rw, "call_writer_field_rewrite", fake_rewrite)
    obj = _sample_object()
    writings = [{
        "candidate_id": "c9",
        "title": "豆包：终端判断 × 收费视频",
        "body": "商业化关注豆包终端；视频号有豆包相关视频。",
        "details": [
            "商业化团队：豆包 AI 手机判断",
            "视频号团队：豆包收费视频",
        ],
    }]
    rels, skipped = rw.write_relations([obj], writings=writings)
    assert calls["n"] == 1
    assert not skipped
    assert rels
    assert "×" not in (rels[0].get("title") or "")
    assert rels[0].get("_writer_field_rewrite")


def test_title_from_details_shape():
    t = title_from_details(
        ["商业化团队：合同催初稿", "编辑部：商务选题已提报"],
        candidate_title="京东 · 合作",
    )
    assert t.startswith("京东")
    assert "×" not in t
    assert "各知一半" not in t
    assert "催初稿" in t or "合同" in t


def test_title_dual_colon_formula_and_body_gates():
    from app.relation_writer import (
        body_has_closing_cliche,
        body_has_label_leak,
        enforce_narrative_hygiene,
        title_is_formula,
    )

    assert title_is_formula("小宇宙：编辑部计划谈年底合作，商业化在看平台数据")
    assert not title_is_formula("京东合作稿正在催初稿")
    assert body_has_label_leak("编辑部记下面壁进度，商业化记的是保持沟通，两边各知一半。")
    assert body_has_closing_cliche("商业化在催初稿，编辑部已提报，两边进度可对照。")
    _, body, _, flags = enforce_narrative_hygiene(
        title="面壁智能直播",
        body="编辑部记下面壁进度，商业化记的是保持沟通，两边各知一半。",
        details=["编辑部：面壁 IPO", "商业化团队：保持沟通"],
        candidate_title="面壁",
    )
    assert body == ""
    assert flags.get("body_cleared_label_leak") or flags.get("body_cleared_template")
    _, body2, _, flags2 = enforce_narrative_hygiene(
        title="京东合作稿",
        body="商业化在催初稿，编辑部选题已提报，两边进度可对照。",
        details=["商业化团队：催初稿", "编辑部：选题提报"],
        candidate_title="京东",
    )
    assert "可对照" not in body2
    assert flags2.get("body_cleared_cliche")


def test_bare_person_title_rebuilt_or_flagged():
    from app.relation_gate import relation_fails_grounding
    from app.relation_writer import (
        enforce_narrative_hygiene,
        narrative_violation_codes,
        title_is_bare_person_name,
    )

    assert title_is_bare_person_name("Arvin Sun")
    assert title_is_bare_person_name("Brad Yuan")
    assert not title_is_bare_person_name("xAI")
    assert not title_is_bare_person_name("陈宇森任 Alibaba Cloud LATAM 总经理")
    assert not title_is_bare_person_name("SunBoy Venture Fund 投资人 Arvin Sun")

    title, _, _, flags = enforce_narrative_hygiene(
        title="Arvin Sun",
        body="",
        details=["硅谷 BD 团队：孙邻家在 SunBoy Venture Fund 做投资"],
        candidate_title="Arvin Sun",
    )
    assert not title_is_bare_person_name(title)
    assert flags.get("title_rebuilt")
    assert "投资" in title or "SunBoy" in title or "孙" in title

    # 无法从 details 扩写时 → 仍为人名 → grounding 否决不成卡
    bad = {
        "title": "Arvin Sun",
        "body": "",
        "details": ["硅谷 BD 团队：Arvin Sun"],
        "evidence": [{"team": "硅谷 BD 团队", "snippet": "Arvin Sun", "item_id": 1}],
        "weak": True,
        "decision_tier": "watch",
    }
    # hygiene 可能仍得到人名；violation + fails_grounding 双保险
    codes = narrative_violation_codes("Arvin Sun", "", ["硅谷 BD 团队：Arvin Sun"])
    assert "title_bare_person" in codes
    # 强制用人名标题测 gate
    assert any("人名" in e for e in relation_fails_grounding(bad, []))


def test_writer_audit_trace_on_write(monkeypatch):
    from app import relation_writer as rw
    from app.relation_writer_audit import finalize_writer_audit

    monkeypatch.setattr(rw, "call_writer_field_rewrite", lambda tasks: {})
    obj = _sample_object()
    rels, skipped = rw.write_relations(
        [obj],
        writings=[{
            "candidate_id": "c9",
            "title": "豆包两条线",
            "body": "商业化关注终端，视频号有相关视频。",
            "details": ["商业化团队：豆包 AI 手机判断", "视频号团队：豆包收费视频"],
        }],
    )
    assert not skipped
    assert rels[0].get("_writer_trace")
    assert any(e.get("kind") == "first_write" for e in rels[0]["_writer_trace"]["events"])
    audit = finalize_writer_audit(rels)
    assert audit["summary"]["n_formed"] == 1
    assert audit["rows"]
