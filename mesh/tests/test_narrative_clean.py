"""Narrative 展示层清理规则测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.narrative_clean import clean_detail_line, clean_source_label


def test_strip_wrong_team_attribution_prefix():
    r = clean_source_label("硅谷 BD 团队建联记录 · 与面壁智能詹杨帆的对话", "编辑部")
    assert r.action == "strip_prefix"
    assert r.cleaned.startswith("团队建联记录")
    assert "面壁" in r.cleaned
    assert "硅谷 BD" not in r.cleaned.split("·")[0]


def test_keep_matching_owner_prefix():
    r = clean_source_label("编辑部沟通记录 · 与千问的对话", "编辑部")
    assert r.action == "keep"
    assert r.cleaned == r.original


def test_needs_review_when_team_is_content():
    r = clean_source_label("硅谷 BD 团队与面壁智能合作方案讨论", "编辑部")
    assert r.action == "needs_review"
    assert r.cleaned == r.original


def test_never_changes_owner():
    """清理函数不接收/返回 owner 修改 — 只测 label。"""
    r = clean_source_label("硅谷 BD 团队建联记录", "编辑部")
    assert r.action == "strip_prefix"
    # owner 仍由调用方持有，clean 结果不含团队归属宣称
    assert "硅谷 BD 团队" not in r.cleaned


def test_detail_neutralize_wrong_team():
    r = clean_detail_line("硅谷 BD 团队记录：终端是 AI 进入物理世界的路径。", "编辑部")
    assert r.action == "neutralize"
    assert r.cleaned.startswith("记录：")
    assert "终端" in r.cleaned


def test_detail_keep_correct_team():
    r = clean_detail_line("编辑部记录：端云协同。", "编辑部")
    assert r.action == "keep"
