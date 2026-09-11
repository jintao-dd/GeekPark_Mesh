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


def test_cli_doc_search_uses_drive_search_bot(monkeypatch):
    captured = {}

    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        captured["argv"] = list(argv)
        captured["as"] = as_identity
        from app.agent.feishu_hands.normalize import envelope_ok

        return envelope_ok([], tool=tool, meta={"cli": {"ok": True, "data": {"items": []}}})

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "周报", "resource_type": "doc", "max_results": 5},
        timeout_sec=5,
        user_access_token="",
    )
    assert env.ok
    assert captured["as"] == "bot"
    assert captured["argv"][:2] == ["drive", "+search"]
    assert "--page-size" in captured["argv"]
    assert "--limit" not in captured["argv"]


def test_cli_doc_search_fallback_docs_search_with_uat(monkeypatch):
    calls = []

    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        calls.append((list(argv), as_identity))
        from app.agent.feishu_hands.normalize import envelope_fail, envelope_ok

        if argv[:2] == ["drive", "+search"]:
            return envelope_fail("cli:scope_denied:x", tool=tool)
        return envelope_ok([], tool=tool, meta={"cli": {"ok": True, "data": {"items": []}}})

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "周报", "resource_type": "doc"},
        timeout_sec=5,
        user_access_token="u-test",
    )
    assert env.ok
    assert calls[0][0][:2] == ["drive", "+search"]
    assert calls[1][0][:2] == ["docs", "+search"]
    assert calls[1][1] == "user"


def test_cli_doc_create_parses_nested_document_and_grants(monkeypatch):
    calls = []

    def fake_cli_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        calls.append(list(argv))
        from app.agent.feishu_hands.normalize import envelope_ok

        if argv[:2] == ["docs", "+create"]:
            return envelope_ok(
                [],
                tool=tool,
                meta={
                    "cli": {
                        "ok": True,
                        "data": {
                            "document": {
                                "document_id": "docx_tok_nested",
                                "url": "https://geek.feishu.cn/docx/docx_tok_nested",
                            }
                        },
                    }
                },
            )
        # share / member-add
        return envelope_ok([], tool=tool, meta={"cli": {"ok": True}})

    monkeypatch.setattr(backends, "_cli_run", fake_cli_run)
    env = backends._cli_call(
        "feishu.doc.create",
        {"title": "T", "content": "body", "confirmed": True},
        timeout_sec=10,
        open_id="ou_test_user",
    )
    assert env.ok
    assert env.items and env.items[0]["url"].endswith("docx_tok_nested")
    assert (env.meta or {}).get("tenant_share") is True
    assert (env.meta or {}).get("member_grant") is True
    assert any(a[:2] == ["drive", "+member-add"] for a in calls)
    assert any(a[:3] == ["drive", "permission.public", "patch"] for a in calls)


def test_cli_extract_created_doc_nested():
    token, url = backends._cli_extract_created_doc(
        {
            "document": {
                "document_id": "abc",
                "url": "https://geek.feishu.cn/docx/abc",
            }
        }
    )
    assert token == "abc"
    assert url.endswith("/docx/abc")


def test_cli_group_uses_chat_list(monkeypatch):
    calls = []

    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        calls.append(list(argv))
        from app.agent.feishu_hands.normalize import envelope_ok

        if argv[:2] == ["im", "+chat-search"]:
            return envelope_ok(
                [],
                tool=tool,
                meta={"cli": {"ok": True, "data": {"chats": None, "total": 0}}},
            )
        assert argv[:2] == ["im", "+chat-list"]
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "chats": [
                            {"name": "Mesh Lab", "chat_id": "oc_aaa"},
                            {"name": "Other", "chat_id": "oc_bbb"},
                        ]
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "Mesh", "resource_type": "group", "max_results": 5},
        timeout_sec=5,
    )
    assert env.ok
    # list fallback returns all visible chats; no local utterance filter
    assert len(env.items) == 2
    assert calls[0][:2] == ["im", "+chat-search"]
    assert calls[1][:2] == ["im", "+chat-list"]


def test_cli_calendar_uses_freebusy_for_open_id(monkeypatch):
    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        assert argv[:2] == ["calendar", "+freebusy"]
        assert "--user-id" in argv
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "users": [
                            {
                                "user_id": "ou_x",
                                "raw_busy": [
                                    {
                                        "start_time": "2026-09-13T12:30:00+08:00",
                                        "end_time": "2026-09-13T13:00:00+08:00",
                                        "rsvp_status": "accept",
                                    }
                                ],
                            }
                        ]
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.calendar.list",
        {"days": 7},
        timeout_sec=5,
        open_id="ou_x",
    )
    assert env.ok
    assert len(env.items) == 1
    assert "12:30" in str(env.items[0].get("snippet") or "")


def test_cli_message_prefers_chat_messages_list(monkeypatch):
    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        assert argv[:2] == ["im", "+chat-messages-list"]
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "messages": [
                            {
                                "content": "CLI火凤凰测试",
                                "create_time": "2026-09-11 12:00",
                                "message_id": "om_1",
                                "sender": {"id": "ou_x"},
                            }
                        ]
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "火凤凰", "resource_type": "message", "chat_id": "oc_1", "max_results": 5},
        timeout_sec=5,
    )
    assert env.ok
    assert env.items and "火凤凰" in str(env.items[0].get("snippet") or env.items[0].get("title") or "")


def test_cli_calendar_keeps_busy_even_if_nl_query_present(monkeypatch):
    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        assert argv[:2] == ["calendar", "+freebusy"]
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "users": [
                            {
                                "user_id": "ou_x",
                                "raw_busy": [
                                    {
                                        "start_time": "2026-09-13T12:30:00+08:00",
                                        "end_time": "2026-09-13T13:00:00+08:00",
                                        "rsvp_status": "accept",
                                    }
                                ],
                            }
                        ]
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.calendar.list",
        {"query": "whatever full sentence", "days": 7},
        timeout_sec=5,
        open_id="ou_x",
    )
    assert env.ok
    assert len(env.items) == 1


def test_cli_group_list_no_local_utterance_filter(monkeypatch):
    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        if argv[:2] == ["im", "+chat-search"]:
            return envelope_ok(
                [],
                tool=tool,
                meta={"cli": {"ok": True, "data": {"chats": None, "total": 0}}},
            )
        assert argv[:2] == ["im", "+chat-list"]
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {"chats": [{"name": "CCC Tech", "chat_id": "oc_1"}]},
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "list all groups please long utterance", "resource_type": "group", "max_results": 5},
        timeout_sec=5,
    )
    assert env.ok
    assert len(env.items) == 1
    assert env.items[0]["title"] == "CCC Tech"


def test_cli_member_list(monkeypatch):
    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        assert argv[:2] == ["im", "+chat-members-list"]
        assert "--chat-id" in argv
        assert argv[argv.index("--chat-id") + 1] == "oc_1"
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "users": [
                            {"member_id": "ou_a", "name": "Alice"},
                            {"member_id": "ou_b", "name": "Bob"},
                        ]
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "", "resource_type": "member", "chat_id": "oc_1", "max_results": 8},
        timeout_sec=5,
    )
    assert env.ok
    assert len(env.items) == 2
    assert env.items[0]["title"] == "Alice"
    assert env.items[0]["docs_type"] == "member"


def test_cli_user_get(monkeypatch):
    def fake_run(argv, *, timeout_sec, tool, as_identity="bot", user_access_token="", confirm_yes=False):
        from app.agent.feishu_hands.normalize import envelope_ok

        assert argv[:2] == ["contact", "+get-user"]
        assert "--user-id" in argv
        uid = argv[argv.index("--user-id") + 1]
        return envelope_ok(
            [],
            tool=tool,
            meta={
                "cli": {
                    "ok": True,
                    "data": {
                        "user": {
                            "name": "杜锦涛",
                            "employee_no": "G-356",
                            "enterprise_email": "dujintao@geekpark.net",
                            "mobile": "+8617600000000",
                            "open_id": uid,
                        }
                    },
                }
            },
        )

    monkeypatch.setattr(backends, "_cli_run", fake_run)
    env = backends._cli_call(
        "feishu.search",
        {"query": "", "resource_type": "user", "open_ids": ["ou_x"], "max_results": 3},
        timeout_sec=5,
    )
    assert env.ok
    assert env.items[0]["title"] == "杜锦涛"
    assert "G-356" in env.items[0]["snippet"]
    assert "+86176" not in env.items[0]["snippet"]


def test_build_ask_args_user_injects_mentions():
    from app.agent.colleague_v3 import _build_ask_args
    from app.agent.models import AgentContext, IssueRef
    from app.agent.session_state import SessionContextState

    ctx = AgentContext(
        scope_key="s",
        channel="feishu_group",
        chat_id="oc_1",
        issue_ref=IssueRef(mode="none"),
        mentions=[{"open_id": "ou_m", "name": "张三"}],
    )
    a = _build_ask_args(
        "feishu.search",
        "他是谁",
        {"tool": "feishu.search", "resource_type": "user", "query": ""},
        ctx,
        SessionContextState(),
    )
    assert a.get("resource_type") == "user"
    assert a.get("open_ids") == ["ou_m"]


def test_build_ask_args_does_not_stuff_full_utterance_into_calendar_q():
    from app.agent.colleague_v3 import _build_ask_args

    a = _build_ask_args(
        "feishu.calendar.list",
        "\u5217\u4e00\u4e0b\u63a5\u4e0b\u6765\u51e0\u5929\u7684\u65e5\u7a0b",
        {"tool": "feishu.calendar.list", "query": "\u5217\u4e00\u4e0b\u63a5\u4e0b\u6765\u51e0\u5929\u7684\u65e5\u7a0b"},
        None,
    )
    assert a.get("q") in ("", None)


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
