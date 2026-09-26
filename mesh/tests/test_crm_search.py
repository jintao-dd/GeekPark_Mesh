"""CRM search unit tests (local sqlite with synced data)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.agent import crm_search as cs
from app.agent.models import IdentityResult, PermissionDecision, AgentContext, IssueRef


def test_crm_search_gavin_take():
    con = db.connect()
    if not cs._table_ready(con):
        pytest.skip("local CRM empty")
    out = cs.search_crm(con, query="Gavin Ni", mode="take")
    assert out["ok"]
    assert out["takes"]
    assert any("Gavin" in (t.get("person") or "") for t in out["takes"])
    assert "硅谷 CRM" in out["text"] or "思琪" in out["text"]


def test_crm_search_auto_hits_without_phrase_routing():
    con = db.connect()
    if not cs._table_ready(con):
        pytest.skip("local CRM empty")
    out = cs.search_crm(con, query="Gavin Ni", mode="auto")
    assert out["ok"]
    assert out["takes"] or out["people"] or out["interactions"]
    assert any("Gavin" in str(x) for x in (
        [t.get("person") for t in out.get("takes") or []]
        + [p.get("name") for p in out.get("people") or []]
    ))


def test_crm_search_recent_interactions():
    con = db.connect()
    if not cs._table_ready(con):
        pytest.skip("local CRM empty")
    out = cs.search_crm(con, query="最近沟通", mode="recent")
    assert out["ok"]
    assert out["interactions"]
    assert out["interactions"][0].get("date")


def test_crm_tool_no_email_leak():
    con = db.connect()
    if not cs._table_ready(con):
        pytest.skip("local CRM empty")
    ident = IdentityResult(status="bound", feishu_open_id="ou_x", primary_team="品牌创意团队")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["crm.search"],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
        published_only=True,
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )
    r = cs.tool_crm_search(con, ident, perm, ctx, {"query": "Yuna"})
    assert r.ok
    blob = str(r.payload)
    assert "wechat" not in blob.lower() or "WeChat" not in str(r.payload.get("text") or "")
    # payload people dicts must not include email
    for p in r.payload.get("people") or []:
        assert "email" not in p
