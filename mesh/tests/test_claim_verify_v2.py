"""Ask claim verify v2：item_id/chunk_id 绑定。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ask_analysis import _claim_supported


def test_claim_bound_to_item_id():
    contexts = [{
        "期号": "2026-8-17",
        "章节": "抽取条目",
        "标题": "面壁智能",
        "内容": "编辑部沟通：端云协同与路由模型判断。",
        "归属团队": "编辑部",
        "条目ID": 677,
    }]
    source_reports = [{
        "group_id": "g1",
        "source": "编辑部",
        "issue": "2026-8-17",
        "items": contexts,
        "evidence": [{
            "ref": "e1",
            "quote": "编辑部沟通：端云协同与路由模型判断。",
            "item_id": 677,
        }],
    }]
    claim = {
        "text": "编辑部沟通涉及端云协同。",
        "evidence_refs": ["e1"],
    }
    assert _claim_supported(claim["text"], claim, contexts, source_reports) == "keep"


def test_claim_reject_when_item_mismatch():
    contexts = [{
        "期号": "2026-8-17",
        "章节": "抽取条目",
        "标题": "面壁智能",
        "内容": "编辑部沟通：端云协同。",
        "归属团队": "编辑部",
        "条目ID": 677,
    }]
    source_reports = [{
        "group_id": "g1",
        "items": contexts,
        "evidence": [{"ref": "e1", "quote": "编辑部沟通：端云协同。", "item_id": 677}],
    }]
    claim = {
        "text": "硅谷 BD 团队已与高通签署芯片独家协议。",
        "evidence_refs": ["e1"],
    }
    assert _claim_supported(claim["text"], claim, contexts, source_reports) == "reject"


def test_citation_bindings():
    from app import ask_citation

    contexts = [{
        "期号": "2026-8-17",
        "章节": "抽取条目",
        "标题": "面壁智能 · 张三",
        "内容": "编辑部沟通记录端云协同。",
        "归属团队": "编辑部",
        "条目ID": 100,
        "chunk_id": "item:2026-8-17:100",
    }]
    ans = "编辑部与张三沟通了端云协同。\n来源：2026-8-17"
    out = ask_citation.validate_answer(ans, contexts)
    assert out["bindings"]
    assert out["bindings"][0].get("item_ids") == ["100"]
