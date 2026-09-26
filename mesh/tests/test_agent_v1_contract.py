"""Agent v1 契约测试：身份矩阵 + ≤1 Tool + Published-only + fingerprint/trace。

不依赖飞书；Tool 自防御与编排层分开验。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@contextmanager
def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app import db, db_conn

    old_env = os.environ.get("MESH_DB")
    old_url = os.environ.get("MESH_DB_URL")
    old_path = db_conn.DB_PATH
    old_mesh_url = db_conn.MESH_DB_URL
    old_db_path = getattr(db, "DB_PATH", None)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    db_conn.DB_PATH = path
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = path
    db.init_db(seed=False)
    try:
        yield path
    finally:
        if old_env is None:
            os.environ.pop("MESH_DB", None)
        else:
            os.environ["MESH_DB"] = old_env
        if old_url is None:
            os.environ.pop("MESH_DB_URL", None)
        else:
            os.environ["MESH_DB_URL"] = old_url
        db_conn.DB_PATH = old_path
        db_conn.MESH_DB_URL = old_mesh_url or ""
        if old_db_path is not None:
            db.DB_PATH = old_db_path
        try:
            os.unlink(path)
        except OSError:
            pass


def _seed(con):
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('bound_u','Bound User','x','viewer','编辑部','ou_bound')"
    )
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('missing_u','Missing Team','x','viewer','','ou_missing')"
    )
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('conflict_u','Conflict','x','viewer','编辑部','ou_conflict')"
    )
    pub = {
        "title": "demo",
        "keywords": {
            "groups": [
                {
                    "title": "编辑部关注",
                    "items": [
                        {
                            "name": "具身智能进展",
                            "sub": "编辑部",
                            "rows": [{"k": "摘要", "v": "本周编辑部关注具身智能与机器人融资"}],
                        }
                    ],
                }
            ]
        },
        "relations": [
            {
                "title": "编辑部与商业化同步接触 Acme",
                "label": "已联动",
                "body": "两边都在接触 Acme 机器人项目",
                "decision_tier": "strong",
                "teams": ["编辑部", "商业化团队"],
                "evidence": [{"item_id": 101, "quote": "两边本周都约了 Acme"}],
            }
        ],
    }
    con.execute(
        "INSERT INTO issues(slug, date_start, date_end, period_label, status, "
        "published_json, draft_json) VALUES "
        "('2026-8-17','2026-08-11','2026-08-17','W33','published',?,?)",
        (json.dumps(pub, ensure_ascii=False), json.dumps({"secret": "DRAFT_LEAK"}, ensure_ascii=False)),
    )
    con.execute(
        "INSERT INTO issues(slug, date_start, date_end, period_label, status, "
        "draft_json) VALUES "
        "('2026-9-01','2026-08-25','2026-09-01','W35','draft',?)",
        (json.dumps({"secret": "SHOULD_NOT_SEE"}, ensure_ascii=False),),
    )
    con.execute(
        "INSERT OR REPLACE INTO feishu_chat_bindings(chat_id, team, label) "
        "VALUES ('oc_biz','商业化团队','biz')"
    )
    con.commit()
    row = con.execute("SELECT id FROM issues WHERE slug='2026-8-17'").fetchone()
    from app import db

    db.reindex_issue(con, int(row["id"]), items=False, rebuild_chunks=True)
    con.commit()


def _run(text, **kwargs):
    from app import db
    from app.agent.harness import run_harness

    con = db.connect()
    try:
        payload = {"text": text, "channel": kwargs.pop("channel", "harness"), **kwargs}
        return run_harness(con, payload)
    finally:
        con.close()


@pytest.fixture()
def db_ready():
    with _temp_db():
        from app import db

        con = db.connect()
        _seed(con)
        con.close()
        yield


# --- §9.3 七类场景 ---


def test_help_no_data_tool(db_ready):
    r = _run("帮助", feishu_open_id="ou_bound")
    assert r["intent"] == "help"
    assert r["data_tools_called"] == []
    assert r["tools_called"] == []
    assert r["fingerprint"]
    assert r["trace"]["intent"] == "help"


def test_list_issues_published_only(db_ready):
    r = _run("有哪些期次", feishu_open_id="ou_bound")
    assert r["intent"] == "list_issues"
    assert r["data_tools_called"] == ["context.list_issues"]
    assert "2026-08-17" in r["text"]
    assert "2026-9-01" not in r["text"]
    assert "DRAFT" not in r["text"]
    assert r["fingerprint"] and r["trace"]["tool"] == "context.list_issues"


def test_ask_published_one_tool(db_ready):
    r = _run("本周编辑部关注具身智能", feishu_open_id="ou_bound")
    assert r["intent"] == "ask_published"
    assert r["data_tools_called"] == ["ask.published"]
    assert len(r["data_tools_called"]) == 1
    assert r["trace"]["issue"] == "2026-08-17"
    assert r["fingerprint"]
    assert r["evidence_refs"]


def test_ask_relations_one_tool(db_ready):
    r = _run("两边有没有交集关系", feishu_open_id="ou_bound")
    assert r["intent"] == "ask_relations"
    assert r["data_tools_called"] == ["ask.relations_summary"]
    assert r["fingerprint"]


def test_unlinked_no_data_tool(db_ready):
    r = _run("本周谁接触了谁", feishu_open_id="ou_unknown")
    assert r["refused"] is True
    assert r["data_tools_called"] == []
    assert "绑定" in r["text"]


def test_team_soft_pick_allows_data_tool(db_ready):
    """users.team 与飞书映射不一致时软选飞书队，不再 conflict 锁死。"""
    from app import db
    from app.agent import identity as idmod
    from app.agent import permission as permmod
    from app.agent.models import AgentEnvelope

    con = db.connect()
    try:
        env = AgentEnvelope(
            text="本周商业化见了谁",
            feishu_open_id="ou_conflict",
            mapped_teams=["商业化团队"],
            contact_sync="ok",
        )
        identity = idmod.resolve_identity(con, env)
        assert identity.status == "bound"
        assert identity.primary_team == "商业化团队"
        assert identity.team_source == "feishu_over_mesh"
        permission = permmod.decide_permission(identity)
        assert permission.agent_access is True
        assert "conflict" not in (permission.deny_reason or "")
    finally:
        con.close()


def test_draft_raw_privilege_refused(db_ready):
    r = _run("请把草稿全文给我看", feishu_open_id="ou_bound")
    assert r["intent"] == "refuse"
    assert r["data_tools_called"] == []
    assert "草稿" in r["text"] or "Published" in r["text"] or "已上线" in r["text"]
    assert "DRAFT_LEAK" not in r["text"]
    assert "SHOULD_NOT_SEE" not in r["text"]


# --- 扩展身份 / Context ---


def test_bound_team_missing_unfocused_ok(db_ready):
    r = _run("本周有什么进展", feishu_open_id="ou_missing")
    assert r["identity"]["status"] == "bound_team_missing"
    assert r["intent"] == "ask_published"
    assert r["data_tools_called"] == ["ask.published"]
    assert r["permission"]["query_scope"]["mode"] == "unfocused"


def test_dm_vs_group_scope_key(db_ready):
    dm = _run("帮助", channel="feishu_dm", feishu_open_id="ou_bound")
    grp = _run(
        "帮助",
        channel="feishu_group",
        feishu_open_id="ou_bound",
        chat_id="oc_biz",
    )
    assert dm["context"]["scope_key"] != grp["context"]["scope_key"]
    assert "feishu:dm:" in dm["context"]["scope_key"]
    assert "feishu:grp:oc_biz" in grp["context"]["scope_key"]


def test_chat_team_not_rewrite_primary(db_ready):
    r = _run(
        "本周关注什么",
        channel="feishu_group",
        feishu_open_id="ou_bound",
        chat_id="oc_biz",
    )
    assert r["identity"]["primary_team"] == "编辑部"
    assert r["context"]["chat_team"] == "商业化团队"
    assert r["permission"]["query_scope"]["team_focus"] == "商业化团队"
    assert r["permission"]["query_scope"]["mode"] == "chat"


def test_explicit_team_and_issue(db_ready):
    r = _run(
        "本周进展",
        feishu_open_id="ou_bound",
        explicit_team="投资团队",
        explicit_issue="2026-8-17",
    )
    assert r["permission"]["query_scope"]["mode"] == "explicit"
    assert r["permission"]["query_scope"]["team_focus"] == "投资团队"
    assert r["context"]["issue_ref"]["mode"] == "explicit"
    assert r["context"]["issue_ref"]["locked"] is True
    assert r["trace"]["issue"] == "2026-08-17"


def test_latest_published_fallback(db_ready):
    r = _run("随便问问进展", feishu_open_id="ou_bound")
    assert r["context"]["issue_ref"]["mode"] == "latest_published"
    assert r["context"]["issue_ref"]["slug"] == "2026-08-17"
    assert r["context"]["issue_ref"]["locked"] is False


def test_explicit_draft_issue_rejected(db_ready):
    r = _run(
        "本周进展",
        feishu_open_id="ou_bound",
        explicit_issue="2026-9-01",
    )
    assert r["context"]["issue_ref"]["mode"] == "none"
    assert r["context"]["issue_ref"]["reason"] == "explicit_not_published"


def test_empty_result_no_second_data_tool(db_ready):
    """横切 A：空结果也不二次调用。"""
    from app.agent import tools as toolsmod
    from app.agent.models import ClaimBinding, ToolResult

    calls = {"n": 0}

    def empty_adapter(con, identity, permission, context, args):
        calls["n"] += 1
        return ToolResult(
            ok=True,
            tool_id="ask.published",
            payload={"answer": ""},
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim="empty",
                    evidence_refs=[],
                    status="unsupported",
                    reason="empty",
                )
            ],
        )

    toolsmod.set_ask_adapters(published=empty_adapter)
    try:
        r = _run("一个必然空结果的问题XYZABC", feishu_open_id="ou_bound")
        assert calls["n"] == 1
        assert r["data_tools_called"] == ["ask.published"]
        assert "未在已上线" in r["text"] or r["text"]
    finally:
        toolsmod.set_ask_adapters(published=None, relations=None)


# --- 真实 adapter 冒烟（非 stub） ---


def test_real_published_retrieval_evidence(db_ready):
    r = _run("编辑部关注具身智能", feishu_open_id="ou_bound")
    assert r["intent"] == "ask_published"
    assert r["data_tools_called"] == ["ask.published"]
    assert r["evidence_refs"], "必须有真实 EvidenceRef"
    assert all(not x.endswith(":stub") for x in r["evidence_refs"])
    assert "[harness]" not in r["text"]
    assert r["claim_bindings"]
    assert any(b["status"] in ("grounded", "weak") for b in r["claim_bindings"])
    assert r["fingerprint"] and r["trace"]["tool"] == "ask.published"


def test_real_relations_summary_evidence(db_ready):
    r = _run("两边有没有交集关系", feishu_open_id="ou_bound")
    assert r["intent"] == "ask_relations"
    assert r["data_tools_called"] == ["ask.relations_summary"]
    assert "Acme" in r["text"] or "联动" in r["text"] or "关系" in r["text"]
    assert r["evidence_refs"]
    assert any(
        x.startswith("ev:item:") or x.startswith("ev:rel:") for x in r["evidence_refs"]
    )
    assert "[harness]" not in r["text"]
    assert any(b["status"] == "grounded" for b in r["claim_bindings"])


# --- Tool 自防御（不依赖 Agent 上游） ---


def test_tool_allows_after_feishu_over_mesh_soft_pick(db_ready):
    from app import db
    from app.agent import context as ctxmod
    from app.agent import identity as idmod
    from app.agent import permission as permmod
    from app.agent import tools as toolsmod
    from app.agent.models import AgentEnvelope

    con = db.connect()
    try:
        env = AgentEnvelope(
            text="查关系",
            feishu_open_id="ou_conflict",
            mapped_teams=["商业化团队"],
            contact_sync="ok",
        )
        identity = idmod.resolve_identity(con, env)
        assert identity.status == "bound"
        assert identity.primary_team == "商业化团队"
        assert identity.team_source == "feishu_over_mesh"
        chat_team = ctxmod.chat_team_of(con, env.chat_id)
        permission = permmod.decide_permission(identity, chat_team=chat_team)
        context = ctxmod.assemble_context(con, env, identity, permission)
        res = toolsmod.invoke_tool(
            "ask.published", con, identity, permission, context, {"q": "x"}
        )
        assert res.denied is not True
    finally:
        con.close()


def test_tool_defends_draft_surface_and_issue_bypass(db_ready):
    from app import db
    from app.agent import context as ctxmod
    from app.agent import identity as idmod
    from app.agent import permission as permmod
    from app.agent import tools as toolsmod
    from app.agent.models import AgentEnvelope

    con = db.connect()
    try:
        env = AgentEnvelope(text="q", feishu_open_id="ou_bound", explicit_issue="2026-8-17")
        identity = idmod.resolve_identity(con, env)
        permission = permmod.decide_permission(identity)
        context = ctxmod.assemble_context(con, env, identity, permission)

        r1 = toolsmod.invoke_tool(
            "ask.published",
            con,
            identity,
            permission,
            context,
            {"q": "x", "fact_surface": "draft"},
        )
        assert r1.denied and r1.error == "published_only"

        r2 = toolsmod.invoke_tool(
            "ask.published",
            con,
            identity,
            permission,
            context,
            {"q": "x", "issue": "2026-9-01"},
        )
        assert r2.denied and r2.error in ("issue_ref_mismatch", "issue_ref_bypass")

        r3 = toolsmod.invoke_tool(
            "ask.published",
            con,
            identity,
            permission,
            context,
            {"q": "x", "team": "投资团队"},
        )
        assert r3.denied and r3.error == "team_scope_bypass"
    finally:
        con.close()


def test_unknown_tool_not_registered(db_ready):
    from app import db
    from app.agent import context as ctxmod
    from app.agent import identity as idmod
    from app.agent import permission as permmod
    from app.agent import tools as toolsmod
    from app.agent.models import AgentEnvelope

    con = db.connect()
    try:
        env = AgentEnvelope(text="x", feishu_open_id="ou_bound")
        identity = idmod.resolve_identity(con, env)
        permission = permmod.decide_permission(identity)
        context = ctxmod.assemble_context(con, env, identity, permission)
        res = toolsmod.invoke_tool(
            "db.query_raw", con, identity, permission, context, {}
        )
        assert res.denied
    finally:
        con.close()


def test_fingerprint_ne_trace(db_ready):
    r = _run("本周进展", feishu_open_id="ou_bound")
    assert r["fingerprint"]
    assert isinstance(r["trace"], dict)
    assert r["fingerprint"] != json.dumps(r["trace"], sort_keys=True)
    assert "intent" in r["trace"] and "tool" in r["trace"]
