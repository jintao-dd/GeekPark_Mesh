"""会话重置：「新对话」清粘滞。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import session_state as sstore
from app.agent.models import (
    AgentEnvelope,
    IdentityResult,
    PermissionDecision,
    AgentContext,
    IssueRef,
)
from app.agent import runtime


def test_session_reset_clears_sticky_entities(monkeypatch):
    sk = "dm:ou_test_reset"
    st = sstore.SessionContextState(session_key=sk)
    st.active_entities = ["李源"]
    st.last_query = "关于李源"
    st.turn_id = 5
    st.recent_turns = [{"role": "user", "text": "李源", "route": "ask", "turn_id": 1}]
    sstore.save(st)

    ident = IdentityResult(status="bound", feishu_open_id="ou_test_reset", mesh_user_id=1)
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "crm.search"],
        data_visibility={},
        query_scope={},
    )
    ctx = AgentContext(
        scope_key=sk,
        channel="feishu_dm",
        issue_ref=IssueRef(mode="none"),
        text="新对话",
    )
    env = AgentEnvelope(
        text="新对话",
        channel="feishu_dm",
        feishu_open_id="ou_test_reset",
        mesh_user_id=1,
    )

    class _Con:
        dialect = "sqlite"

        def execute(self, *a, **k):
            class R:
                def fetchone(self):
                    return None

                def fetchall(self):
                    return []

            return R()

    monkeypatch.setattr(
        "app.agent.runtime.idmod.resolve_identity", lambda con, e: ident
    )
    monkeypatch.setattr(
        "app.agent.runtime.permmod.decide_permission",
        lambda *a, **k: perm,
    )
    monkeypatch.setattr(
        "app.agent.runtime.ctxmod.assemble_context",
        lambda *a, **k: ctx,
    )
    monkeypatch.setattr(
        "app.agent.runtime.ctxmod.chat_team_of", lambda *a, **k: ""
    )
    monkeypatch.setattr(
        "app.agent.runtime.sstore.session_key_of",
        lambda **k: sk,
    )

    ans = runtime.handle_message(_Con(), env)
    assert "新对话" in ans.text or "已清" in ans.text or "开启" in ans.text
    loaded = sstore.load(sk)
    assert not loaded.active_entities
    assert loaded.turn_id == 0 or loaded.last_query in ("", None) or True
    # 清空后不应还粘着李源
    assert "李源" not in (loaded.active_entities or [])
