"""CLI Adapter unit bits — no real Feishu / no binary required."""
from __future__ import annotations

from unittest import mock

from app.agent.feishu_hands import backends


def test_cli_subprocess_env_maps_feishu_app(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    monkeypatch.delenv("LARKSUITE_CLI_APP_ID", raising=False)
    monkeypatch.delenv("LARKSUITE_CLI_APP_SECRET", raising=False)
    env = backends._cli_subprocess_env()
    assert env["LARKSUITE_CLI_APP_ID"] == "cli_test_app"
    assert env["LARKSUITE_CLI_APP_SECRET"] == "sec_test"
    assert env["LARKSUITE_CLI_BRAND"] == "feishu"


def test_cli_error_code_scope_denied():
    err = backends._cli_error_code(
        "feishu_api_99991672: Access denied",
        {"ok": False, "error": {"code": 99991672, "message": "Access denied"}},
    )
    assert "scope_denied" in err


def test_cli_run_injects_as_bot_and_format(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)

        class P:
            returncode = 0
            stdout = '{"ok": true, "data": {}}'
            stderr = ""

        return P()

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends, "_cli_bin", lambda: "lark-cli")
    env = backends._cli_run(
        ["docs", "+search", "--query", "x"], timeout_sec=5, tool="feishu.search"
    )
    assert env.ok
    assert captured["cmd"][0] == "lark-cli"
    assert "--as" in captured["cmd"] and "bot" in captured["cmd"]
    assert "--format" in captured["cmd"] and "json" in captured["cmd"]


def test_cli_doc_create_calls_tenant_share(monkeypatch):
    def fake_cli_run(argv, *, timeout_sec, tool):
        from app.agent.feishu_hands.normalize import envelope_ok

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

    called = {}

    def fake_share(token, *, docs_type="docx"):
        called["token"] = token
        called["docs_type"] = docs_type
        return {"code": 0}

    monkeypatch.setattr(backends, "_cli_run", fake_cli_run)
    with mock.patch(
        "app.agent.feishu_hands.native.open_tenant_readable", side_effect=fake_share
    ):
        env = backends._cli_call(
            "feishu.doc.create",
            {"title": "T", "content": "body", "confirmed": True},
            timeout_sec=10,
        )
    assert env.ok
    assert called.get("token") == "docx_tok_1"
    assert (env.meta or {}).get("tenant_share") is True
