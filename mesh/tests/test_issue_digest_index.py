"""「本期概览」入索引：导语/KPI 必须可检索，「最新一期讲了什么」才答得出。

背景：2026-09-08 期 published_json 里 lead(255字)/kpis/views 齐全，但 chunk_index
只索引 relations/contacts/keywords/plans/views/gaps/data_sources —— lead 与 kpis
从不入索引，导致「最新一期讲了什么」只能捞到零散条目标题。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, db_conn, search


@pytest.fixture
def digest_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(db_conn, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(seed=False)
    con = db.connect()
    yield con
    con.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def _seed_issue(con, slug="2026-09-08", lead="本期首次进入记录的公司与人：文飞、牛一鸣。",
                kpis=None, period_label="2026.9.8"):
    pub = {
        "slug": slug,
        "period_label": period_label,
        "lead": lead,
        "kpis": kpis if kpis is not None else [
            {"n": "21", "label": "可同步的关系"},
            {"n": "24", "label": "接触过的人"},
        ],
        "relations": [
            {"title": "英伟达视频播客", "label": "已联动", "body": "商业化做赞助", "evidence": [{}]},
        ],
        "contacts": [],
    }
    con.execute(
        "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
        "VALUES(?,?,?,?,?,?,?)",
        (slug, "2026-09-01", "2026-09-08", period_label, "published",
         json.dumps(pub, ensure_ascii=False), json.dumps(pub, ensure_ascii=False)),
    )
    iid = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()["id"]
    db.reindex_issue(con, iid)
    con.commit()
    return iid


def test_digest_row_written_to_fts(digest_db):
    _seed_issue(digest_db)
    n = digest_db.execute(
        "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=? AND section='本期概览'",
        ("2026-09-08",),
    ).fetchone()["c"]
    assert n == 1


def test_digest_body_contains_lead_and_kpis(digest_db):
    _seed_issue(digest_db)
    row = digest_db.execute(
        "SELECT title, body FROM search_fts WHERE section='本期概览'"
    ).fetchone()
    assert "文飞" in row["body"]
    assert "21 可同步的关系" in row["body"]
    assert "24 接触过的人" in row["body"]
    assert "2026.9.8" in row["body"]
    assert "2026.9.8" in row["title"]


def test_digest_flows_into_chunk_index(digest_db):
    """chunk_index 从 search_fts 物化，概览必须跟着进（否则 Ask 检索看不到）。"""
    from app import chunk_index

    iid = _seed_issue(digest_db)
    chunk_index.rebuild_issue(digest_db, iid)
    digest_db.commit()
    row = digest_db.execute(
        "SELECT section, title, body FROM chunk_index WHERE issue_slug=? AND section='本期概览'",
        ("2026-09-08",),
    ).fetchone()
    assert row is not None
    assert "文飞" in row["body"]


def test_digest_searchable_by_latest_question(digest_db):
    """「最新一期讲了什么」应能命中概览行（而不是只捞到零散条目标题）。"""
    _seed_issue(digest_db)
    hits = search.fts_search(digest_db, "最新一期周报讲了什么", slug="2026-09-08")
    sections = [h.get("section") for h in hits]
    assert "本期概览" in sections, sections


def test_digest_ranked_above_scattered_items(digest_db):
    """概览的章节权重（5）应高于抽取条目（2），排更前。"""
    _seed_issue(digest_db)
    hits = search.fts_search(digest_db, "本期周报讲了什么", slug="2026-09-08")
    if not hits:
        pytest.skip("FTS 未命中，跳过排序断言")
    assert hits[0].get("section") == "本期概览", [(h.get("section"), h.get("title")) for h in hits[:5]]


def test_digest_absent_when_lead_and_kpis_empty(digest_db):
    """导语与 KPI 都空时不写空壳概览。"""
    _seed_issue(digest_db, lead="", kpis=[])
    n = digest_db.execute(
        "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=? AND section='本期概览'",
        ("2026-09-08",),
    ).fetchone()["c"]
    assert n == 0


def test_digest_only_kpis_still_written(digest_db):
    """只有 KPI 没有导语时也要写（仍能答「本期规模」）。"""
    _seed_issue(digest_db, lead="", kpis=[{"n": "24", "label": "接触过的人"}])
    row = digest_db.execute(
        "SELECT body FROM search_fts WHERE section='本期概览'"
    ).fetchone()
    assert row is not None and "24 接触过的人" in row["body"]


def test_draft_issue_still_no_digest(digest_db):
    """草稿期不得进索引（含概览）。"""
    pub = {"slug": "d1", "period_label": "草稿期", "lead": "泄露内容", "kpis": [], "relations": []}
    digest_db.execute(
        "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
        "VALUES(?,?,?,?,?,?,?)",
        ("d1", "2026-01-01", "2026-01-07", "草稿期", "draft",
         json.dumps(pub, ensure_ascii=False), json.dumps(pub, ensure_ascii=False)),
    )
    iid = digest_db.execute("SELECT id FROM issues WHERE slug='d1'").fetchone()["id"]
    db.reindex_issue(digest_db, iid)
    digest_db.commit()
    n = digest_db.execute(
        "SELECT COUNT(*) c FROM search_fts WHERE issue_slug='d1'"
    ).fetchone()["c"]
    assert n == 0
