"""CRM stats / cross / 门控单测。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, db_conn
from app.agent import crm_search as cs
from app.agent import crm_cross
from app.agent.feishu_reply import format_display_text
from app.agent.models import AgentAnswer


@pytest.fixture
def crm_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db_conn, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(seed=False)
    con = db.connect()
    con.execute(
        "INSERT INTO crm_people(notion_id, display_name, aliases, company_names) VALUES(?,?,?,?)",
        ("p1", "Alice Zhang", "Alice", "Acme"),
    )
    con.execute(
        "INSERT INTO crm_people(notion_id, display_name, aliases, company_names) VALUES(?,?,?,?)",
        ("p2", "Bob Li", "", "Beta"),
    )
    con.execute(
        "INSERT INTO crm_companies(notion_id, name, aliases) VALUES(?,?,?)",
        ("c1", "Acme", "ACME Inc"),
    )
    con.execute(
        "INSERT INTO crm_companies(notion_id, name) VALUES(?,?)",
        ("c2", "Beta"),
    )
    con.execute(
        "INSERT INTO crm_interactions(notion_id, title, date_start, interact_type, people_names) "
        "VALUES(?,?,?,?,?)",
        ("i1", "meet Alice", "2026-09-10", "沟通会", "Alice Zhang"),
    )
    con.execute(
        "INSERT INTO crm_interactions(notion_id, title, date_start, interact_type, people_names) "
        "VALUES(?,?,?,?,?)",
        ("i2", "prep Alice", "2026-09-09", "会前准备", "Alice Zhang"),
    )
    con.execute(
        "INSERT INTO crm_interactions(notion_id, title, date_start, interact_type, people_names) "
        "VALUES(?,?,?,?,?)",
        ("i3", "old Bob", "2024-01-01", "DM", "Bob Li"),
    )
    con.execute(
        "INSERT INTO crm_takes(notion_id, name, person_names, verdict, last_reviewed) "
        "VALUES(?,?,?,?,?)",
        ("t1", "take1", "Alice Zhang", "值得跟进", "2026-09-10"),
    )
    # 周报侧：Alice 也在；Charlie 仅周报
    con.execute(
        "INSERT INTO issues(slug,date_start,date_end,period_label,status,published_json) "
        "VALUES(?,?,?,?,?,?)",
        ("2026-09-08", "2026-09-01", "2026-09-08", "t", "published", "{}"),
    )
    con.execute(
        "INSERT INTO entity_team_facts(issue_slug,date_end,section,name,team,kind) "
        "VALUES(?,?,?,?,?,?)",
        ("2026-09-08", "2026-09-08", "接触", "Alice Zhang", "硅谷 BD 团队", "unknown"),
    )
    con.execute(
        "INSERT INTO entity_team_facts(issue_slug,date_end,section,name,team,kind) "
        "VALUES(?,?,?,?,?,?)",
        ("2026-09-08", "2026-09-08", "接触", "Charlie", "硅谷 BD 团队", "unknown"),
    )
    con.commit()
    yield con
    con.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_infer_metric_archive_vs_touched():
    assert cs.infer_crm_metric("CRM一共有多少人") == "people_archive"
    assert cs.infer_crm_metric("硅谷团队近一年接触了多少人") == "people_touched"
    assert cs.infer_crm_metric("CRM有多少家公司") == "companies"


def test_is_crm_count_question():
    assert cs.is_crm_count_question("硅谷团队近一年接触了多少人")
    assert cs.is_crm_count_question("CRM一共有多少人")
    assert not cs.is_crm_count_question("编辑部接触了多少人")
    assert not cs.is_crm_count_question("硅谷团队最近周报讲了什么")


def test_stats_people_archive(crm_db):
    out = cs.stats_crm(crm_db, metric="people_archive")
    assert out["total"] == 2
    assert out["sample_n"] <= 2
    assert out["total"] != out["sample_n"] or out["total"] == 2
    assert "档案" in out["text"]
    assert "不是已上线周报" in out["text"]


def test_stats_people_touched_dedup_and_window(crm_db):
    out = cs.stats_crm(crm_db, metric="people_touched", date_from="2025-09-01")
    # Alice 两次 → 1 人；Bob 在窗外
    assert out["total"] == 1
    assert out["event_count"] == 2
    assert out["sample"][0]["name"] == "Alice Zhang"
    assert out["sample"][0]["events"] == 2


def test_stats_companies(crm_db):
    out = cs.stats_crm(crm_db, metric="companies")
    assert out["total"] == 2


def test_search_crm_explicit_stats_mode(crm_db):
    # 意图由 planner 决定并显式传 mode=stats+metric；search_crm 只忠实执行
    out = cs.search_crm(
        crm_db,
        query="硅谷团队近一年接触了多少人",
        mode="stats",
        metric="people_touched",
    )
    assert out["mode"] == "stats"
    assert out["metric"] == "people_touched"
    assert out["total"] == 1  # Alice only in window ~1 year from today (2026)


def test_search_crm_auto_stays_auto(crm_db):
    # mode=auto 时不再用正则猜 stats；保持 auto，交给 planner 决意图
    out = cs.search_crm(crm_db, query="硅谷团队近一年接触了多少人", mode="auto")
    assert out["mode"] != "stats"


def test_cross_both(crm_db):
    out = crm_cross.cross_crm_weekly(crm_db, op="both", entity_kind="person")
    assert out["counts"]["both"] >= 1
    assert any(r.get("crm") == "Alice Zhang" for r in out["rows"])


def test_cross_crm_only(crm_db):
    out = crm_cross.cross_crm_weekly(crm_db, op="crm_only", entity_kind="person")
    names = [r["name"] for r in out["rows"]]
    assert "Bob Li" in names
    assert "Alice Zhang" not in names


def test_cross_weekly_only(crm_db):
    out = crm_cross.cross_crm_weekly(crm_db, op="weekly_only", entity_kind="person")
    names = [r["name"] for r in out["rows"]]
    assert "Charlie" in names


def test_ask_planner_suppresses_weekly_count_for_crm():
    from app import ask_planner

    plan = ask_planner.plan_retrieval("硅谷团队近一年接触了多少人")
    # 不应走周报 structured count
    assert plan.set_op != "count"


def test_feishu_reply_crm_footer_not_weekly():
    ans = AgentAnswer(
        text="结论：档案 2 人",
        intent="crm_search",
        evidence_refs=["crm:stats:people_archive:2"],
        tools_called=["crm.search"],
        trace={"source_tier": "crm_prior"},
    )
    display = format_display_text(ans, payload={"source_tier": "crm_prior"})
    assert "硅谷 CRM" in display
    assert "已上线周报" not in display
