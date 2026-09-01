"""Ask citation / 弱关系闸门 / 上传预拆 单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_citation, relation_gate
from app.aggregator import SplitResult, Segment, sources_from_split, split_hint
import json


def test_citation_strips_wrong_team_attribution():
    contexts = [
        {
            "期号": "2026-8-17",
            "章节": "抽取条目",
            "标题": "面壁智能 · 张三",
            "内容": "编辑部沟通：端云协同。",
            "归属团队": "编辑部",
        }
    ]
    answer = (
        "硅谷 BD 团队与张三讨论了终端路径。"
        "编辑部记录了面壁智能的端云协同。"
        "\n来源：2026-8-17 · 抽取条目 · 面壁智能"
    )
    out = ask_citation.validate_answer(answer, contexts)
    assert "硅谷 BD" not in out["answer"] or "张三" not in out["answer"].split("编辑部")[0]
    assert any("张三" in f and "硅谷" in f for f in out["flags"])
    assert "编辑部" in out["answer"]
    assert "来源：" in out["answer"]


def test_citation_keeps_grounded():
    contexts = [
        {
            "期号": "2026-8-17",
            "章节": "抽取条目",
            "标题": "面壁智能 · 张三",
            "内容": "编辑部沟通记录。",
            "归属团队": "编辑部",
        }
    ]
    answer = "编辑部与张三沟通了端云协同。\n来源：2026-8-17 · 抽取条目 · 面壁智能"
    out = ask_citation.validate_answer(answer, contexts)
    assert out["removed"] == []
    assert "编辑部" in out["answer"]


def test_relation_gate_allows_weak_blocks_needs_review():
    draft = {
        "relations": [{
            "label": "一方接触，另一方用得上",
            "weak": True,
            "title": "某公司 · 某人",
            "teams": ["编辑部"],
            "details": ["编辑部记录：x"],
            "sources": ["编辑部"],
        }]
    }
    errs = relation_gate.relation_publish_blockers(draft, [])
    assert not any("弱关系" in e for e in errs)

    draft2 = {
        "relations": [{
            "label": "合作机会",
            "weak": False,
            "needs_review": True,
            "title": "测试",
            "teams": ["编辑部"],
            "evidence": [{"item_id": 1, "team": "编辑部", "snippet": "x"}],
        }]
    }
    errs2 = relation_gate.relation_publish_blockers(draft2, [])
    assert any("叙事待核对" in e for e in errs2)


def test_relation_gate_blocks_fake_cross_team():
    items = [
        {"id": 1, "source_id": 23, "owner_team": "编辑部", "pointer": "面壁—张三",
         "entities": '["张三","面壁"]', "blocked": 0},
        {"id": 2, "source_id": 23, "owner_team": "硅谷 BD 团队", "pointer": "面壁—张三",
         "entities": '["张三"]', "blocked": 1},
    ]
    draft = {
        "relations": [{
            "label": "一方接触，另一方用得上",
            "weak": False,
            "title": "面壁 · 张三",
            "teams": ["编辑部", "硅谷 BD 团队"],
            "details": [
                "编辑部记录：a",
                "硅谷 BD 团队记录：b",
            ],
            "sources": ["编辑部", "硅谷 BD 团队"],
        }]
    }
    errs = relation_gate.relation_publish_blockers(draft, items)
    assert errs


def test_sources_from_split_presplit_meta():
    split = SplitResult(
        segments=[
            Segment(title="编辑部 · 沟通", text="aaa", stype="T1", team="编辑部", owner_hint="编辑部"),
            Segment(title="硅谷 BD · 建联", text="bbb", stype="T3", team="硅谷 BD 团队", owner_hint="硅谷 BD 团队"),
        ],
        mode="multi",
        boundaries=2,
    )
    rows = sources_from_split(split, parent_title="数据聚合", parent_filename="agg.docx")
    assert len(rows) == 2
    assert rows[0]["team"] == "编辑部"
    assert rows[1]["stype"] == "T3"
    assert rows[0]["meta"]["split"]["mode"] == "pre_split"
    hint = split_hint(json.dumps(rows[0]["meta"], ensure_ascii=False))
    assert "预拆" in hint
