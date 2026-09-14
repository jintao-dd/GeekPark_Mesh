"""Work memory for planner — no utterance routing."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.supervisor.plan import work_memory_block
from app.agent.supervisor.verify import verify
from app.agent.supervisor.types import TaskGraph, TieredEnvelope


def test_work_memory_includes_identity_people_and_history():
    ident = SimpleNamespace(
        display_hint="测试",
        primary_team="品牌创意团队",
        feishu_open_id="ou_x",
        status="bound",
        person={"name": "测试"},
    )
    session = SimpleNamespace(
        active_entities=["张鹏"],
        active_team="硅谷 BD 团队",
        last_query="最近和老板相关的事情有哪些？",
        recent_turns=[
            {"role": "user", "text": "最近和老板相关的事情有哪些？"},
            {"role": "assistant", "text": "周报这边没查到。"},
        ],
        pending_write=None,
    )
    people = [SimpleNamespace(alias="老板", canonical="张鹏")]
    block = work_memory_block(identity=ident, session=session, resolved_people=people)
    assert "张鹏" in block
    assert "老板" in block
    assert "品牌创意团队" in block
    assert "上一问" in block
    assert "周报这边没查到" in block


def test_work_memory_ignores_answer_chrome_entities():
    ident = SimpleNamespace(
        display_hint="杜锦涛",
        primary_team="品牌创意团队",
        feishu_open_id="ou_x",
        status="bound",
        person={"name": "杜锦涛"},
    )
    session = SimpleNamespace(
        active_entities=["已上线周报", "缺的那一块", "10:30–12:00", "张鹏"],
        active_team="品牌创意团队",
        last_query="和我相关的呢？",
        recent_turns=[],
        pending_write=None,
    )
    block = work_memory_block(identity=ident, session=session, resolved_people=[])
    assert "已上线周报" not in block
    assert "缺的那一块" not in block
    assert "张鹏" in block


def test_verify_empty_with_unused_crm_wants_replan():
    graph = TaskGraph(goal="g", mode="work", band="simple")
    envs = [
        TieredEnvelope(
            step_id="s1",
            worker="org",
            tool="feishu.search",
            ok=True,
            tier="feishu_live",
            text="",
            need_replan=True,
            replan_reason="empty_result",
            payload={"empty": True, "resource_type": "directory"},
        )
    ]
    v = verify(envs, graph=graph)
    assert v["want_replan"] is True
    assert "crm.search" in v["unused_sources"]
    assert v["replan_reason"] == "empty_with_unused_sources"
