# -*- coding: utf-8 -*-
"""Recall Phase 1 · MATCH 配方最小修复（对照诊断结论）。"""
from app import tokenize as tok


def test_r14_drops_pianzi_and():
    m = tok.build_match_query("视频号本周播放较好的片子")
    assert m
    assert "片子" not in m
    assert "播放" in m
    assert "视频" in m or "频号" in m


def test_r12_topic_or_not_and():
    m = tok.build_match_query("端侧模型与智能座舱")
    assert m
    # 并列主题打包为单一 OR 组，不再强制端侧∧座舱
    assert m.count(" AND ") == 0
    assert "端侧" in m or "侧模" in m or "模型" in m
    assert "座舱" in m or "智能" in m
    assert " OR " in m


def test_r11_keeps_governance_drops_bare_numbers():
    m = tok.build_match_query("Agent 治理与合规 7 月 15 日")
    assert m
    assert "7" not in m.split("AND")[0] or '"7"' not in m
    # 裸数字不进
    assert " AND 7 " not in f" {m} "
    assert ' AND "15"' not in m and " AND 15" not in m
    assert "治理" in m or "合规" in m or "agent" in m.lower()
    assert "智能" in m  # Agent↔智能体


def test_r18_latin_soft_drops_plan_scaffold():
    m = tok.build_match_query("Founder Park 下半年规划")
    assert m
    assert "founder" in m.lower()
    assert "park" in m.lower()
    # 不应再 AND 下半年/规划
    assert "规划" not in m
    assert "下半" not in m


def test_r13_long_piece_head_tail_and():
    m = tok.build_match_query("具身智能数据圆桌讨论了什么")
    assert m
    assert "具身" in m
    assert "圆桌" in m or "讨论" in m
    assert " AND " in m
