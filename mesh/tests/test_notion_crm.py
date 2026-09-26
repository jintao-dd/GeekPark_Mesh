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


# ---- Take 页面正文（详细沟通记录）----


def _block(bid, btype, payload, *, children=False):
    return {"id": bid, "type": btype, btype: payload, "has_children": children}


def test_block_text_table_row_and_paragraph():
    row = _block("r1", "table_row", {"cells": [[{"plain_text": "08-24"}], [{"plain_text": "约专访"}]]})
    assert notion_crm._block_text(row) == "08-24 | 约专访"
    para = _block("p1", "paragraph", {"rich_text": [{"plain_text": "首次见面"}]})
    assert notion_crm._block_text(para) == "首次见面"


def test_fetch_page_blocks_walks_children(monkeypatch):
    tree = {
        "page": [
            _block("h", "heading_2", {"rich_text": [{"plain_text": "跟进记录 Log"}]}),
            _block("t", "table", {}, children=True),
            _block("d", "divider", {}),
        ],
        "t": [
            _block("r1", "table_row", {"cells": [[{"plain_text": "09-08"}], [{"plain_text": "稿件梳理"}]]}),
        ],
    }
    monkeypatch.setattr(notion_crm, "list_block_children", lambda bid: tree.get(bid, []))
    blocks = notion_crm.fetch_page_blocks("page")
    types = [b["block_type"] for b in blocks]
    assert types == ["heading_2", "table", "table_row", "divider"]
    # 保序 ord 连续，子块记录了 parent
    assert [b["ord"] for b in blocks] == [0, 1, 2, 3]
    assert blocks[2]["parent_block_id"] == "t"


def test_page_timeline_text_renders_markdown(crm_db):
    con = crm_db
    pid = "take-page-1"
    for i, (btype, text) in enumerate(
        [
            ("paragraph", "2026-08-23 · Take created ahead of first meeting"),
            ("heading_2", "跟进记录 Log"),
            ("table_row", "09-08 | 稿件梳理中"),
            ("table_row", "09-22（当前） | 文章均已发布"),
        ]
    ):
        con.execute(
            "INSERT INTO crm_page_blocks"
            "(notion_id, owner_kind, block_id, parent_block_id, ord, block_type, text,"
            " page_last_edited, synced_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, "takes", f"b{i}", "", i, btype, text, "2026-09-22T00:00:00Z", ""),
        )
    db.commit_retry(con)
    tl = notion_crm.page_timeline_text(con, pid)
    assert "2026-08-23 · Take created" in tl
    assert "## 跟进记录 Log" in tl
    assert "| 09-22（当前） | 文章均已发布 |" in tl


def test_sync_page_blocks_incremental_skips_unchanged(crm_db, monkeypatch):
    con = crm_db
    notion_crm._upsert(
        con,
        "crm_takes",
        "take-1",
        {
            "name": "Bodi",
            "person_ids_json": "[]",
            "person_names": "",
            "verdict": "v",
            "scenario": "",
            "owner": "",
            "last_reviewed": "2026-09-22",
            "is_prospect": "",
            "props_json": "{}",
            "last_edited_time": "2026-09-22T00:00:00Z",
        },
    )
    db.commit_retry(con)
    calls: list[str] = []

    def fake_children(bid):
        calls.append(bid)
        return [_block("b1", "paragraph", {"rich_text": [{"plain_text": "hello"}]})]

    monkeypatch.setattr(notion_crm, "list_block_children", fake_children)
    first = notion_crm.sync_page_blocks(con, "takes")
    assert first["pages_fetched"] == 1
    assert first["blocks_total"] == 1
    # 页面 last_edited 未变 → 第二次不再打 API
    second = notion_crm.sync_page_blocks(con, "takes")
    assert second["pages_fetched"] == 0
    assert calls == ["take-1"]


def test_crm_format_text_includes_timeline():
    from app.agent import crm_search as cs

    text = cs._format_text(
        mode="take",
        query="Bodi 进展",
        people=[],
        companies=[],
        interactions=[],
        takes=[
            {
                "person": "Brad (Bodi) Yuan",
                "name": "Brad (Bodi) Yuan",
                "verdict": "专访完成，等待发布",
                "scenario": "专访",
                "owner": "思琪（Lilyann）",
                "last_reviewed": "2026-09-08",
                "is_prospect": "",
                "timeline": "2026-08-24 · 首次见面\n| 09-22（当前） | 文章均已发布 |",
            }
        ],
    )
    assert "<跟进记录>" in text
    assert "2026-08-24 · 首次见面" in text
    assert "文章均已发布" in text
