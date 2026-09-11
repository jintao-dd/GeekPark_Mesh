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
