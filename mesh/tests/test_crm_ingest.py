"""CRM 增量 → T3「硅谷 BD 团队创业者数据库」接入（生成预览前自动拉取）。

覆盖：增量窗口（上次同步以来变动）、来源标签、抽取落库、无变动不建来源。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import crm_ingest, db


def _seed_issue(con, slug: str = "test-crm-ingest") -> dict:
    con.execute(
        "INSERT OR REPLACE INTO issues(id,slug,date_start,date_end,period_label,status,draft_json) "
        "VALUES (99001,?, '2026-09-16','2026-09-22','测试','draft','{}')",
        (slug,),
    )
    con.execute("DELETE FROM sources WHERE issue_id=99001")
    con.execute("DELETE FROM items WHERE issue_id=99001")
    con.execute("DELETE FROM crm_sync_state")
    con.execute("DELETE FROM crm_people")
    con.execute("DELETE FROM crm_companies")
    con.execute("DELETE FROM crm_interactions")
    con.execute("DELETE FROM crm_takes")
    con.commit()
    return dict(con.execute("SELECT * FROM issues WHERE id=99001").fetchone())


def test_incremental_window_only_changed_rows():
    con = db.connect()
    try:
        issue = _seed_issue(con)
        # 上次同步游标：9/20
        con.execute(
            "INSERT INTO crm_sync_state(kind,cursor_last_edited) VALUES('people','2026-09-20T00:00:00.000Z')"
        )
        con.execute(
            "INSERT INTO crm_people(notion_id,display_name,company_names,headline,last_edited_time) "
            "VALUES('p-old','旧人','A','X','2026-09-19T00:00:00.000Z')"
        )
        con.execute(
            "INSERT INTO crm_people(notion_id,display_name,company_names,headline,last_edited_time) "
            "VALUES('p-new','新人','B','Y','2026-09-21T00:00:00.000Z')"
        )
        con.commit()

        d = crm_ingest.build_incremental_digest(con, since_map={"people": "2026-09-20T00:00:00.000Z"})
        assert d["counts"]["people"] == 1, d["counts"]
        assert "新人" in d["text"] and "旧人" not in d["text"]
        assert issue["slug"] == "test-crm-ingest"
    finally:
        con.close()


def test_ingest_writes_t3_source_with_fixed_label(monkeypatch):
    con = db.connect()
    try:
        issue = _seed_issue(con)
        # 只造 takes，其他表空
        con.execute(
            "INSERT INTO crm_takes(notion_id,name,person_names,verdict,last_edited_time) "
            "VALUES('t1','Yu Su','Yu Su','待见面（9/26）','2026-09-22T03:20:00.000Z')"
        )
        con.commit()
        digest = crm_ingest.build_incremental_digest(con, since_map={})
        assert digest["text"]

        # 桩掉 LLM 抽取，验证来源/标签/归属落库
        def _fake_extract(issue_, *, source_id, text):
            return [
                {
                    "zone": 3, "level": "L1", "kind": "fact",
                    "text": "与 Yu Su 待见面（9/26）。",
                    "entities": ["Yu Su"], "roles": ["资源"], "signals": [],
                    "source_label": crm_ingest.SOURCE_LABEL, "pointer": "take: t1", "blocked": 0,
                }
            ]

        monkeypatch.setattr(crm_ingest, "_extract_items", _fake_extract)
        out = crm_ingest.ingest_for_issue(con, issue, digest=digest)
        con.commit()

        assert out["ingested"] is True and out["items"] == 1
        src = con.execute("SELECT * FROM sources WHERE id=?", (out["source_id"],)).fetchone()
        assert src["channel"] == "crm"
        assert src["stype"] == "T3"
        assert src["team"] == "硅谷 BD 团队"
        it = con.execute("SELECT * FROM items WHERE source_id=?", (out["source_id"],)).fetchone()
        assert it["source_label"] == "硅谷 BD 团队创业者数据库"
        assert it["owner_team"] == "硅谷 BD 团队"
        assert it["channel"] == "crm"

        # 重复接入不新增来源行
        out2 = crm_ingest.ingest_for_issue(con, issue, digest=digest)
        con.commit()
        n = con.execute("SELECT COUNT(*) c FROM sources WHERE issue_id=99001 AND channel='crm'").fetchone()["c"]
        assert n == 1 and out2["source_id"] == out["source_id"]
    finally:
        con.close()


def test_no_changes_makes_no_source(monkeypatch):
    con = db.connect()
    try:
        issue = _seed_issue(con)
        monkeypatch.setenv("MESH_CRM_PREVIEW_INGEST", "1")
        digest = {"text": "", "counts": {"people": 0}, "since": {}, "until": "x"}
        out = crm_ingest.ingest_for_issue(con, issue, digest=digest)
        assert out["ingested"] is False and out["reason"] == "no_changes"
        n = con.execute("SELECT COUNT(*) c FROM sources WHERE issue_id=99001 AND channel='crm'").fetchone()["c"]
        assert n == 0
    finally:
        con.close()


def test_rerun_keeps_earlier_window(monkeypatch):
    """同一期重跑：窗口取更早的 since，不丢上一批增量。"""
    con = db.connect()
    try:
        issue = _seed_issue(con)
        con.execute(
            "INSERT INTO crm_people(notion_id,display_name,last_edited_time) "
            "VALUES('p1','A','2026-09-21T00:00:00.000Z')"
        )
        con.commit()
        monkeypatch.setattr(
            crm_ingest, "_extract_items",
            lambda issue_, *, source_id, text: [
                {"zone": 3, "level": "L1", "kind": "fact", "text": "x",
                 "entities": [], "roles": [], "signals": [],
                 "source_label": crm_ingest.SOURCE_LABEL, "pointer": "", "blocked": 0}
            ],
        )
        d1 = {"text": "第一批", "counts": {"people": 1}, "since": {"people": "2026-09-21T00:00:00.000Z"}, "until": "u1"}
        crm_ingest.ingest_for_issue(con, issue, digest=d1)
        con.commit()
        prev = crm_ingest._prev_since(
            con.execute("SELECT meta FROM sources WHERE issue_id=99001 AND channel='crm'").fetchone()["meta"]
        )
        assert prev["people"] == "2026-09-21T00:00:00.000Z"
    finally:
        con.close()


def test_disabled_env_short_circuits(monkeypatch):
    monkeypatch.setenv("MESH_CRM_PREVIEW_INGEST", "0")
    out = crm_ingest.maybe_ingest_for_issue("test-crm-ingest")
    assert out["ingested"] is False and out["reason"] == "disabled"
