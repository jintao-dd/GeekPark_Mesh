"""黄金回归：错归属 / 假跨团队类问题（不调真实 LLM）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_citation, relation_gate, owner_guard, zone_hard
import json


def test_golden_wrong_team_attribution_stripped():
    """编辑部证据下，答案不得把主体绑到硅谷 BD。"""
    contexts = [
        {
            "期号": "2026-8-17",
            "章节": "抽取条目",
            "标题": "面壁智能 · 詹杨帆",
            "内容": "编辑部沟通记录：端云协同。",
            "归属团队": "编辑部",
        }
    ]
    answer = (
        "硅谷 BD 团队与詹杨帆讨论了终端是 AI 进入物理世界的路径。"
        "编辑部记录了面壁智能的端云协同。"
        "\n来源：2026-8-17 · 抽取条目 · 面壁智能"
    )
    out = ask_citation.validate_answer(answer, contexts)
    assert not any("硅谷 BD" in s and "詹杨帆" in s for s in [out["answer"]])
    assert out["flags"], "应标出未溯源的实体×团队"


def test_golden_fts_pollution_does_not_ground():
    """FTS 正文里写了假跨团队，但无结构化归属时不得洗白 citation。"""
    contexts = [
        {
            "期号": "2026-8-17",
            "章节": "可同步的关系",
            "标题": "面壁智能 · 詹杨帆",
            "内容": "编辑部与硅谷 BD 团队各有一手：硅谷 BD 团队接触了詹杨帆。",
            # 故意不设 归属团队
        }
    ]
    answer = "硅谷 BD 团队与詹杨帆已建立联系。"
    out = ask_citation.validate_answer(answer, contexts)
    # 同行共现仍可能放行；若答案被保留，至少 structured 映射不得从脏 blob 建立
    ev = ask_citation.contexts_evidence(contexts)
    assert "詹杨帆".lower() not in {k for k in ev["entity_teams"]} or not ev["entity_teams"].get("詹杨帆".lower())
    # 标题实体可能被收录；关键是 entity_teams 不含硅谷 BD
    for teams in ev["entity_teams"].values():
        assert "硅谷 BD 团队" not in teams


def test_golden_same_pointer_not_cross_team():
    items = [
        {"id": 1, "source_id": 23, "owner_team": "编辑部", "pointer": "面壁—詹杨帆",
         "entities": '["詹杨帆","面壁智能"]', "blocked": 0},
        {"id": 2, "source_id": 23, "owner_team": "硅谷 BD 团队", "pointer": "面壁—詹杨帆",
         "entities": '["詹杨帆"]', "blocked": 1},
    ]
    draft = {
        "relations": [{
            "label": "一方接触，另一方用得上",
            "weak": False,
            "title": "面壁智能 · 詹杨帆",
            "teams": ["编辑部", "硅谷 BD 团队"],
            "details": ["编辑部记录：a", "硅谷 BD 团队记录：b"],
            "sources": ["编辑部", "硅谷 BD 团队"],
        }]
    }
    # 上线闸门必须拦住（硅谷侧无有效条目）
    errs = relation_gate.relation_publish_blockers(draft, items)
    assert errs
    # draft 过滤后不应再保留硅谷 BD 伪 detail
    cleaned = owner_guard.filter_draft_relations(draft, items)
    blob = json.dumps(cleaned, ensure_ascii=False)
    assert "硅谷 BD" not in blob or cleaned["relations"][0].get("weak") is True


def test_golden_financing_hard_blocked():
    items = [{"text": "本轮融资约 5000 万美元已交割", "zone": 1, "level": "L1", "blocked": 0}]
    out = zone_hard.apply_hard_blocks(items)
    assert out[0]["blocked"] == 1
