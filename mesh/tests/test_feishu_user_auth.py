from __future__ import annotations

import json
from pathlib import Path

from app.agent import feishu_user_auth as uauth
from app.agent.feishu_hands.identity_policy import IdentityNeed, need_for_tool
from app.agent.tools import _auth_required, _ensure_identity
from app.agent.models import IdentityResult


def test_identity_policy_directory_is_bot():
    assert need_for_tool("feishu.search", resource_type="directory") == IdentityNeed.BOT
    assert need_for_tool("feishu.search", resource_type="member") == IdentityNeed.BOT
    assert need_for_tool("feishu.calendar.create") == IdentityNeed.USER
    assert need_for_tool("feishu.calendar.list") == IdentityNeed.USER_PREFERRED


def test_uat_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_DATA_DIR", str(tmp_path))
    uauth.save_user_token(
        "ou_test",
        access_token="u-abc",
        refresh_token="r-1",
        expires_in=3600,
    )
    assert uauth.load_user_token("ou_test") == "u-abc"


def test_ensure_identity_blocks_calendar_create_without_uat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_DATA_DIR", str(tmp_path))
    ident = IdentityResult(status="bound", feishu_open_id="ou_x")
    uat, block = _ensure_identity("feishu.calendar.create", ident, {})
    assert uat == ""
    assert block is not None
    assert block.error == "user_auth_required"
    assert "授权" in (block.payload or {}).get("auth_text", "")


def test_ensure_identity_allows_directory_without_uat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_DATA_DIR", str(tmp_path))
    ident = IdentityResult(status="bound", feishu_open_id="ou_x")
    uat, block = _ensure_identity(
        "feishu.search", ident, {}, resource_type="directory"
    )
    assert block is None
    assert uat == ""


def test_parse_calendar_range_tomorrow_afternoon():
    from app.agent.colleague_v3 import _parse_calendar_range

    s, e = _parse_calendar_range("帮我创建一个明天下午两点的日历，需要我去吃饭")
    assert s and e
    assert "T14:00:00" in s or "T14:00:00+" in s
    assert "T15:00:00" in e or "T15:00:00+" in e


def test_format_display_skips_weekly_footer_for_feishu_write():
    from app.agent.feishu_reply import format_display_text
    from app.agent.models import AgentAnswer

    ans = AgentAnswer(
        text="准备创建日程：去吃饭\n时间：x ~ y",
        intent="feishu_write",
        context={"issue_ref": {"slug": "2026-09-08"}},
        trace={},
    )
    text = format_display_text(ans)
    assert "已上线周报" not in text
