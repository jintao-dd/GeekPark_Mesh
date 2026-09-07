"""聚合包拆段单元测试（不依赖 LLM / DB）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.aggregator import (
    parse_content_stats_toc,
    should_pre_explode,
    split_bundle_ex,
    split_confidence,
)


PAD = "详细内容补充行。\n" * 12

SAMPLE = f"""📊 内容统计
仅壳段落不应单独落库

📆 会议日程
BEGIN:VCALENDAR
grip.events 某活动
{PAD}

视频号数据：
视频标题 | 完播率
某视频 | 12%
{PAD}

TechCrunch-综合 - 综合
作者: 张三
链接: https://example.com
发布时间: 2026-01-01
{PAD}

GP 工作进展周报
前沿社活跃度 80%
{PAD}
"""


def test_multi_segments():
    r = split_bundle_ex(SAMPLE, source_title="内容中心·数据聚合")
    assert len(r.segments) >= 3
    assert r.mode == "multi"
    types = {s.stype for s in r.segments}
    assert "T11" in types or "T3" in types


def test_skip_shell_with_inner_boundaries():
    inner = """📊 内容统计
说明文字

视频号数据：
完播率 视频标题
abc | 1%
""" + "x" * 100
    r = split_bundle_ex(inner)
    assert len(r.segments) >= 1
    assert r.segments[0].stype == "T11"


def test_fallback_never_empty_on_long_text():
    plain = "📊 内容统计\n" + ("说明行无章节边界。\n" * 20)
    r = split_bundle_ex(plain, source_title="误标聚合.txt")
    assert len(r.segments) == 1
    assert r.mode == "fallback"
    assert r.warnings
    assert r.segments[0].stype != "T13"


def test_gp_internal_boundary():
    text = "GP 工作进展周报\n" + ("进展条目。\n" * 20)
    r = split_bundle_ex(text)
    assert r.segments[0].stype == "T10"


MD_REPORT_SNIPPET = """## 📊 内容统计

- **飞书多维表格**: 31 条记录
  - 编辑部 · 选题: 15 条
  - 编辑部 · 沟通记录: 8 条
  - 视频号数据: 8 条
- **TechCrunch**: 11 篇
- **会议日程 (ICS)**: 1 条

## 🤖 AI智能分析

长篇 AI 解读不应进入抽取段落。""" + ("分析填充。\n" * 30) + """

## 🏢 内部飞书内容

### 📆 会议日程 (ICS)

**grip.events 会议日程**
""" + PAD + """

### 📊 飞书多维表格

### 编辑部 · 选题

*表格 ID: tbl*
""" + PAD + """

### 编辑部 · 沟通记录

*表格 ID: tbl2*
""" + PAD + """

### 视频号数据

视频标题 | 完播率
某视频 | 12%
""" + PAD + """

## 🌐 外部信息源

### 📰 TechCrunch

**TechCrunch-综合 - 综合**

作者: 张三
链接: https://example.com
发布时间: 2026-01-01
""" + PAD


def test_md_report_skips_ai_and_splits_by_toc_sections():
    r = split_bundle_ex(MD_REPORT_SNIPPET, source_title="内容聚合报告")
    assert r.mode == "multi"
    assert len(r.segments) >= 5
    stypes = {s.stype for s in r.segments}
    assert "T2" in stypes or "T1" in stypes
    assert "T11" in stypes
    assert "T7" in stypes
    assert all("AI智能分析" not in s.text for s in r.segments)
    toc = parse_content_stats_toc(MD_REPORT_SNIPPET)
    names = {x["name"] for x in toc}
    assert "飞书多维表格" in names
    assert "编辑部 · 选题" in names
    assert "视频号数据" in names
    assert split_confidence(r) in ("high", "medium")
    assert should_pre_explode(r) is (split_confidence(r) == "high")


def test_fallback_is_low_confidence_no_pre_explode():
    plain = "📊 内容统计\n" + ("说明行无章节边界。\n" * 20)
    r = split_bundle_ex(plain, source_title="误标聚合.txt")
    assert r.mode == "fallback"
    assert split_confidence(r) == "low"
    assert not should_pre_explode(r)


if __name__ == "__main__":
    test_multi_segments()
    test_skip_shell_with_inner_boundaries()
    test_fallback_never_empty_on_long_text()
    test_gp_internal_boundary()
    test_md_report_skips_ai_and_splits_by_toc_sections()
    test_fallback_is_low_confidence_no_pre_explode()
    print("ok")
