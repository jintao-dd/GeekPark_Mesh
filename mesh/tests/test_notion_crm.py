"""Notion CRM sync unit tests（不打真实 API）。"""
from __future__ import annotations

import json
import os

import pytest

from app import db, notion_crm


@pytest.fixture()
def crm_db(tmp_path, monkeypatch):
    path = str(tmp_path / "crm.db")
    monkeypatch.setenv("MESH_DB", path)
    monkeypatch.delenv("MESH_DB_URL", raising=False)
    # 重载连接路径
    import app.db_conn as db_conn

    db_conn.DB_PATH = path
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = path
    db.init_db(seed=False)
    con = db.connect()
    notion_crm.ensure_crm_schema(con)
    yield con
    con.close()


def test_parse_notion_id_from_app_url():
    url = "https://app.notion.com/p/Database-Company-3c44df98f91c80c59a77eb49d8971c77"
    assert notion_crm.parse_notion_id(url) == "3c44df98-f91c-80c5-9a77-eb49d8971c77"
    assert notion_crm._infer_kind_from_url(url) == "companies"


def test_ensure_schema_and_upsert(crm_db):
    con = crm_db
    notion_crm._upsert(
        con,
        "crm_people",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        {
            "display_name": "测试人",
            "aliases": "",
            "headline": "CEO",
            "company_ids_json": "[]",
            "company_names": "",
            "sector": "AI",
            "location": "SF",
            "email": "",
            "wechat": "",
            "linkedin": "",
            "interaction_ids_json": "[]",
            "take_ids_json": "[]",
            "interaction_count": "1",
            "last_touched": "2026-09-01",
            "props_json": "{}",
            "last_edited_time": "2026-09-01T00:00:00.000Z",
        },
    )
    db.commit_retry(con)
    row = con.execute(
        "SELECT display_name FROM crm_people WHERE notion_id=?",
        ("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",),
    ).fetchone()
    assert row["display_name"] == "测试人"


def test_resolve_relation_names(crm_db):
    con = crm_db
    cid = "11111111-1111-1111-1111-111111111111"
    pid = "22222222-2222-2222-2222-222222222222"
    notion_crm._upsert(
        con,
        "crm_companies",
        cid,
        {
            "name": "Acme",
            "aliases": "",
            "one_liner": "",
            "sector": "",
            "stage": "",
            "website": "",
            "people_ids_json": "[]",
            "props_json": "{}",
            "last_edited_time": "",
        },
    )
    notion_crm._upsert(
        con,
        "crm_people",
        pid,
        {
            "display_name": "Ada",
            "aliases": "",
            "headline": "",
            "company_ids_json": json.dumps([cid]),
            "company_names": "",
            "sector": "",
            "location": "",
            "email": "",
            "wechat": "",
            "linkedin": "",
            "interaction_ids_json": "[]",
            "take_ids_json": "[]",
            "interaction_count": "",
            "last_touched": "",
            "props_json": "{}",
            "last_edited_time": "",
        },
    )
    db.commit_retry(con)
    notion_crm.resolve_relation_names(con)
    row = con.execute("SELECT company_names FROM crm_people WHERE notion_id=?", (pid,)).fetchone()
    assert row["company_names"] == "Acme"


def test_page_to_person_fields():
    page = {
        "id": "p1",
        "last_edited_time": "2026-09-01T12:00:00.000Z",
        "properties": {
            "Display name": {"type": "title", "title": [{"plain_text": "Bob"}]},
            "Company": {"type": "relation", "relation": [{"id": "c1"}]},
            "Email": {"type": "email", "email": "a@b.com"},
            "Last touched": {"type": "rollup", "rollup": {"type": "date", "date": {"start": "2026-08-01"}}},
        },
    }
    cols = notion_crm._page_to_person(page)
    assert cols["display_name"] == "Bob"
    assert json.loads(cols["company_ids_json"]) == ["c1"]
    assert cols["email"] == "a@b.com"
    assert cols["last_touched"] == "2026-08-01"
