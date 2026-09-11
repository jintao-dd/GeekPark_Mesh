"""CLI Adapter unit bits — no real Feishu / no binary required."""
from __future__ import annotations

from unittest import mock

from app.agent.feishu_hands import backends


def test_cli_subprocess_env_maps_feishu_app_and_mints_tat(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    monkeypatch.delenv("LARKSUITE_CLI_APP_ID", raising=False)
    monkeypatch.delenv("LARKSUITE_CLI_APP_SECRET", raising=False)
    monkeypatch.delenv("LARKSUITE_CLI_TENANT_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        "app.agent.feishu_api.get_tenant_access_token",
        lambda **kwargs: "t-test-token",
    )
    env = backends._cli_subprocess_env()
    assert env["LARKSUITE_CLI_APP_ID"] == "cli_test_app"
    assert env["LARKSUITE_CLI_APP_SECRET"] == "sec_test"
    assert env["LARKSUITE_CLI_BRAND"] == "feishu"
    assert env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "t-test-token"


def test_cli_error_code_scope_denied():
    err = backends._cli_error_code(
        "feishu_api_99991672: Access denied",
        {"ok": False, "error": {"code": 99991672, "message": "Access denied"}},
    )
    assert "scope_denied" in err


def test_cli_error_code_auth_token():
    err = backends._cli_error_code(
        "no access token available for bot",
        {"ok": False, "error": {"type": "authentication", "subtype": "token_missing"}},
    )
    assert "auth_token" in err


def test_cli_run_injects_as_bot_and_format(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env") or {}

        class P:
            returncode = 0
            stdout = '{"ok": true, "data": {}}'
            stderr = ""

        return P()

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends, "_cli_bin", lambda: "lark-cli")
    monkeypatch.setattr(
        backends,
        "_cli_subprocess_env",
        lambda **kwargs: {"LARKSUITE_CLI_TENANT_ACCESS_TOKEN": "t-x"},
    )
    env = backends._cli_run(
        ["calendar", "+agenda"], timeout_sec=5, tool="feishu.calendar.list", as_identity="bot"
    )
    assert env.ok
    assert captured["cmd"][0] == "lark-cli"
    assert "--as" in captured["cmd"] and "bot" in captured["cmd"]
    assert "--format" in captured["cmd"] and "json" in captured["cmd"]


def test_cli_doc_search_uses_page_size_and_user(monkeypatch):
    captured = {}

    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        captured["argv"] = list(argv)
        captured["as"] = as_identity
        captured["uat"] = user_access_token
        from app.agent.feishu_hands.normalize import envelope_ok

        return envelope_ok([], tool=tool, meta={"cli": {"ok": True, "data": {"items": []}}})

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "周报", "resource_type": "doc", "max_results": 5},
        timeout_sec=5,
        user_access_token="u-test",
    )
    assert env.ok
    assert captured["as"] == "user"
    assert "--page-size" in captured["argv"]
    assert "--limit" not in captured["argv"]
    assert captured["uat"] == "u-test"


def test_cli_doc_search_requires_user_token():
    env = backends._cli_call(
        "feishu.search",
        {"query": "周报", "resource_type": "doc"},
        timeout_sec=5,
        user_access_token="",
    )
    assert not env.ok
    assert "user_token_required" in str(env.error or "")


def test_cli_doc_create_calls_cli_tenant_share(monkeypatch):
    def fake_cli_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        if argv[:3] == ["drive", "permission.public", "patch"]:
            assert confirm_yes is True
            return envelope_ok([], tool=tool, meta={"cli": {"ok": True}})
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "document_id": "docx_tok_1",
                        "url": "https://feishu.cn/docx/docx_tok_1",
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_cli_run)
    env = backends._cli_call(
        "feishu.doc.create",
        {"title": "T", "content": "body", "confirmed": True},
        timeout_sec=10,
    )
    assert env.ok
    assert (env.meta or {}).get("tenant_share") is True
    assert (env.meta or {}).get("backend") == "cli"


def test_format_display_skips_weekly_footer_for_feishu_live():
    from app.agent.feishu_reply import format_display_text
    from app.agent.models import AgentAnswer

    ans = AgentAnswer(
        text="飞书里搜到了两条消息。",
        intent="ask",
        context={"issue_ref": {"slug": "2026-09-08"}},
        trace={"source_tier": "feishu_live"},
    )
    text = format_display_text(ans, payload={"source_tier": "feishu_live"})
    assert "已上线周报" not in text
    assert "飞书里搜到了两条消息" in text
