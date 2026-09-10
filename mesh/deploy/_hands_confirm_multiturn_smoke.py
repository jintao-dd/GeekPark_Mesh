#!/usr/bin/env python3
"""Hands 多轮自测：上下文创建文档 + 跨 worker pending + 短句确认。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["MESH_FEISHU_HANDS"] = "1"
os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
os.environ["MESH_AGENT_SESSION_DIR"] = str(ROOT / "data" / "agent_sessions_smoke")

from app.agent import colleague_v3
from app.agent import session_state as sstore
from app.agent.models import AgentContext, IdentityResult, IssueRef, PermissionDecision
from app.agent.tool_contract import FEISHU_ALL_TOOLS


def _ident():
    return IdentityResult(
        status="bound", primary_team="编辑部", display_hint="小王", feishu_open_id="ou_x"
    )


def _perm():
    return PermissionDecision(
        agent_access=True,
        tool_acl=list(FEISHU_ALL_TOOLS)
        + ["ask.published", "ask.relations_summary", "context.list_issues"],
        data_visibility={"forbid": ["draft", "raw", "unpublished"]},
        query_scope={"mode": "primary", "team_focus": "编辑部"},
        published_only=True,
    )


def main() -> int:
    sstore.reset_for_tests()
    chat = "oc_smoke_ctx"
    ctx = AgentContext(
        scope_key="t", channel="feishu_group", chat_id=chat, issue_ref=IssueRef(mode="none")
    )
    fails: list[str] = []

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            if "介绍一下你自己" in user and "确认" not in user.split("用户：")[-1][:4]:
                return (
                    '{"action":"prepare_write","tool":"feishu.doc.create",'
                    '"args":{"title":"关于 Mesh"}}'
                )
            if "确认" in user.split("用户：")[-1]:
                return '{"action":"confirm_write"}'
            return '{"action":"speak"}'
        if "飞书文档" in system or "用户原话" in user or "完整正文" in user:
            return "我是 Mesh，GeekPark 的 AI 同事。我能查已上线周报，也能在确认后帮你写飞书。"
        return "写入完成。"

    st = sstore.SessionContextState(
        session_key=sstore.session_key_of(channel="feishu_group", chat_id=chat)
    )
    with mock.patch("app.llm.call", side_effect=fake_llm):
        r1 = colleague_v3.handle(
            con=None,
            user_text="创建一个新文档详细的介绍一下你自己",
            identity=_ident(),
            permission=_perm(),
            context=ctx,
            session=st,
            invoke_tool=__import__("app.agent.tools", fromlist=["invoke_tool"]).invoke_tool,
            render_tool_result=lambda r, i, s: ("ok", [], []),
        )
        body = str(((st.pending_write or {}).get("args") or {}).get("content") or "")
        if r1.intent != "feishu_write" or "Mesh" not in body:
            fails.append(f"prepare/intro failed intent={r1.intent} body={body[:80]!r}")

        # 跨 worker：只留磁盘
        sstore._STORE.clear()
        sstore._PENDING_STORE.clear()
        st2 = sstore.SessionContextState(session_key="fresh-worker")
        r2 = colleague_v3.handle(
            con=None,
            user_text="确认",
            identity=_ident(),
            permission=_perm(),
            context=ctx,
            session=st2,
            invoke_tool=__import__("app.agent.tools", fromlist=["invoke_tool"]).invoke_tool,
            render_tool_result=lambda r, i, s: ("created", [], []),
        )
        if r2.tools_called != ["feishu.doc.create"]:
            fails.append(f"cross-worker confirm failed: {r2.tools_called} {r2.text[:80]}")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS hands_context_multiturn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
