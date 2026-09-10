#!/usr/bin/env python3
"""Hands 确认写多轮自测（mock）：复现 root_id / @mention / 复合确认。

退出码 0 = 全过。不碰真实飞书。
"""
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

from app.agent import colleague_v3
from app.agent import session_state as sstore
from app.agent.feishu_bot import parse_im_message
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


def _ctx(chat: str):
    return AgentContext(
        scope_key="t", channel="feishu_group", chat_id=chat, issue_ref=IssueRef(mode="none")
    )


def main() -> int:
    sstore.reset_for_tests()
    chat = "oc_smoke_hands"
    ctx = _ctx(chat)
    fails: list[str] = []

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            raise AssertionError(f"decide should be hard-path, got user={user[:80]!r}")
        return "写入完成（smoke）。"

    # 1) parse: root_id 不得进入 thread_id；@ 必须剥离
    p = parse_im_message(
        {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "chat_id": chat,
                "chat_type": "group",
                "message_type": "text",
                "root_id": "om_card",
                "content": '{"text":"@_user_1 确认"}',
            },
        }
    )
    if not p or p.get("thread_id") or p.get("text") != "确认":
        fails.append(f"parse_im bad: {p}")

    k = sstore.session_key_of(channel="feishu_group", chat_id=chat, thread_id="om_card")
    if k != f"grp:{chat}":
        fails.append(f"session_key fragmented: {k}")

    st = sstore.SessionContextState(session_key=k)
    with mock.patch("app.llm.call", side_effect=fake_llm):
        r1 = colleague_v3.handle(
            con=None,
            user_text="创建一个空的文档",
            identity=_ident(),
            permission=_perm(),
            context=ctx,
            session=st,
            invoke_tool=__import__("app.agent.tools", fromlist=["invoke_tool"]).invoke_tool,
            render_tool_result=lambda r, i, s: (str((r.payload or {}).get("answer") or "ok"), [], []),
        )
        if r1.intent != "feishu_write" or not st.pending_write:
            fails.append(f"prepare failed: intent={r1.intent} pending={st.pending_write}")

        # 2) 新 session 对象 + 带 root 的旧键场景：pending store 回填
        st2 = sstore.SessionContextState(session_key="grp:other_should_not_matter")
        r2 = colleague_v3.handle(
            con=None,
            user_text="@_user_1 确认",
            identity=_ident(),
            permission=_perm(),
            context=ctx,
            session=st2,
            invoke_tool=__import__("app.agent.tools", fromlist=["invoke_tool"]).invoke_tool,
            render_tool_result=lambda r, i, s: ("已创建", [], []),
        )
        if r2.tools_called != ["feishu.doc.create"] or not r2.trace.get("hard_confirm"):
            fails.append(f"confirm lost pending: tools={r2.tools_called} tr={r2.trace}")

        # 3) 再来一轮：标题+内容 → 复合确认
        st3 = sstore.SessionContextState(session_key=k)
        r3 = colleague_v3.handle(
            con=None,
            user_text="标题：测试 内容：2222",
            identity=_ident(),
            permission=_perm(),
            context=ctx,
            session=st3,
            invoke_tool=__import__("app.agent.tools", fromlist=["invoke_tool"]).invoke_tool,
            render_tool_result=lambda r, i, s: ("ok", [], []),
        )
        if r3.intent != "feishu_write":
            fails.append(f"prepare2 failed: {r3.intent} {r3.text[:80]}")

        captured = {}

        def inv(tool, con, identity, permission, context, arguments):
            captured.update(arguments or {})
            return type(
                "R",
                (),
                {
                    "ok": True,
                    "error": "",
                    "denied": False,
                    "payload": {"answer": "ok"},
                    "evidence_refs": [],
                },
            )()

        r4 = colleague_v3.handle(
            con=None,
            user_text="确认创建一个空文档，内容主要是详细的介绍一下你自己吧",
            identity=_ident(),
            permission=_perm(),
            context=ctx,
            session=st3,
            invoke_tool=inv,
            render_tool_result=lambda r, i, s: ("ok", [], []),
        )
        if r4.tools_called != ["feishu.doc.create"]:
            fails.append(f"compound confirm failed: {r4.tools_called} {r4.text[:80]}")
        if "介绍一下你自己" not in str(captured.get("content") or ""):
            fails.append(f"content not merged: {captured}")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS hands_confirm_multiturn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
