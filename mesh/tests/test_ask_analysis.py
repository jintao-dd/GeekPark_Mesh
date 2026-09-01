"""Phase A 多步分析：分组 / 校验决策（不调真实 LLM）。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_analysis


def test_group_contexts_by_source_and_issue():
    ctxs = [
        {"期号": "2026-8-17", "归属团队": "编辑部", "章节": "接触", "标题": "A", "内容": "编辑部接触了甲"},
        {"期号": "2026-8-17", "归属团队": "硅谷 BD 团队", "章节": "接触", "标题": "B", "内容": "硅谷接触了乙"},
        {"期号": "2026-8-17", "章节": "检索范围", "标题": "", "内容": "近 30 天"},
        {"期号": "2026-8-10", "来源层": "向量", "章节": "抽取条目", "标题": "C", "内容": "某条目"},
    ]
    groups = ask_analysis.group_contexts(ctxs)
    assert len(groups) >= 2
    assert groups[0]["group_id"].startswith("g")
    ids = {g["id"] for g in groups}
    assert any("编辑部" in (g["source"] or "") or "硅谷" in (g["source"] or "") for g in groups)
    assert all(g.get("group_id") for g in groups)


def test_claim_support_decisions():
    contexts = [{"内容": "编辑部本周与面壁智能詹杨帆沟通了端云协同", "标题": "面壁"}]
    reports = [{
        "facts": [{"text": "编辑部与面壁智能沟通", "evidence_refs": ["e1"]}],
        "evidence": [{"ref": "e1", "quote": "编辑部与面壁智能沟通了端云协同"}],
    }]
    claim = {"text": "编辑部与面壁智能沟通了端云协同", "evidence_refs": ["e1"]}
    assert ask_analysis._claim_supported(claim["text"], claim, contexts, reports) in ("keep", "downgrade")
    assert ask_analysis._claim_supported("火星移民计划已签约百亿", {}, contexts, reports) == "reject"


def test_group_skips_query_preamble():
    ctxs = [
        {"期号": "查询说明", "章节": "结构化检索", "标题": "本答案由主体×团队事实表算出", "内容": "差集说明"},
        {"期号": "2026-8-17", "归属团队": "编辑部", "章节": "接触", "标题": "甲公司", "内容": "编辑部接触甲"},
        {"期号": "2026-8-17", "归属团队": "商业化团队", "章节": "接触", "标题": "甲公司", "内容": "商业化跟进甲"},
    ]
    groups = ask_analysis.group_contexts(ctxs)
    assert len(groups) == 2
    assert all(g["issue"] != "查询说明" for g in groups)


def test_structured_reports_no_llm():
    ctxs = [
        {"期号": "查询说明", "章节": "结构化检索", "标题": "x", "内容": "y"},
        {"期号": "2026-8-17", "归属团队": "编辑部", "标题": "面壁智能", "内容": "采访"},
        {"期号": "2026-8-17", "归属团队": "商业化团队", "标题": "面壁智能", "内容": "跟进"},
    ]
    reports = ask_analysis._reports_from_structured_contexts(ctxs)
    assert len(reports) == 2
    names = {r["source"] for r in reports}
    assert "编辑部" in names and "商业化团队" in names
