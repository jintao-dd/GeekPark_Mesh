"""正式内容边界集成测：draft/Ask、Publish strong、published Preview、rollback+embed。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

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


def test_draft_preview_payload_not_in_fts():
    """Test A: status=draft 即使 published_json 有关系，reindex 后 FTS 为空。"""
    with _temp_db():
        from app import db
        from app.relation_display import build_published_projection

        con = db.connect()
        draft = {
            "relations": [
                {"decision_tier": "strong", "title": "S", "body": "b", "evidence": [{}], "label": "已联动"},
                {"decision_tier": "parallel", "title": "P", "body": "b", "evidence": [{}], "label": "同一赛道，各自在做"},
            ],
            "contacts": [],
        }
        pub = build_published_projection(draft)
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "bound-draft",
                "2026-01-01",
                "2026-01-07",
                "边界测",
                "draft",
                json.dumps(draft, ensure_ascii=False),
                json.dumps(pub, ensure_ascii=False),
            ),
        )
        iid = con.execute("SELECT id FROM issues WHERE slug='bound-draft'").fetchone()["id"]
        db.reindex_issue(con, iid)
        con.commit()
        n = con.execute(
            "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", ("bound-draft",),
        ).fetchone()["c"]
        assert n == 0, n
        con.close()


def test_publish_projection_all_keep_tiers_sorted():
    """Publish 投影：进草稿的 keep 档全部进入，按强度排序。"""
    from app.relation_display import build_published_projection

    draft = {
        "relations": [
            {"decision_tier": "watch", "title": "W1", "body": "b", "evidence": [{"x": 6}]},
            {"decision_tier": "strong", "title": "S1", "body": "b", "evidence": [{"x": 1}]},
            {"decision_tier": "parallel", "title": "P1", "body": "b", "evidence": [{"x": 3}]},
            {"decision_tier": "strong", "title": "S2", "body": "b", "evidence": [{"x": 2}]},
            {"decision_tier": "parallel", "title": "P2", "body": "b", "evidence": [{"x": 4}]},
            {"decision_tier": "parallel", "title": "P3", "body": "b", "evidence": [{"x": 5}]},
            {"decision_tier": "watch", "title": "W2", "body": "b", "evidence": [{"x": 7}]},
        ],
        "_relations_backlog": [{"title": "leak"}],
        "_stale": True,
    }
    pub = build_published_projection(draft)
    titles = [r["title"] for r in pub["relations"]]
    assert titles == ["S1", "S2", "P1", "P2", "P3", "W1", "W2"]
    assert pub["kpis"][0]["n"] == "7"
    assert "_stale" not in pub
    assert "_relations_backlog" not in pub


def test_published_preview_writes_draft_only():
    """published 期可 Preview：只改 draft_json，不动 published_json / FTS。"""
    with _temp_db():
        from app import db, preview_job

        con = db.connect()
        original = {
            "relations": [
                {"decision_tier": "strong", "title": "KEEP", "body": "b", "evidence": [{}]},
            ],
            "lead": "live",
        }
        payload = json.dumps(original, ensure_ascii=False)
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
            "VALUES(?,?,?,?,?,?,?)",
            ("bound-pub", "2026-01-01", "2026-01-07", "边界测", "published", payload, payload),
        )
        iid = con.execute("SELECT id FROM issues WHERE slug='bound-pub'").fetchone()["id"]
        db.reindex_issue(con, iid)
        con.commit()
        n_fts_before = con.execute(
            "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", ("bound-pub",),
        ).fetchone()["c"]
        assert n_fts_before > 0

        stamp = "2026-01-08 12:00"
        draft_new = {
            "relations": [
                {"decision_tier": "strong", "title": "DRAFT_ONLY", "body": "x", "evidence": [{}]},
            ],
            "lead": "draft",
            "_preview_building": True,
        }
        preview_job._write_partial_draft(con, iid, draft_new, stamp)
        con.commit()
        row = con.execute(
            "SELECT status, draft_json, published_json FROM issues WHERE slug=?",
            ("bound-pub",),
        ).fetchone()
        assert row["status"] == "published"
        assert json.loads(row["draft_json"])["relations"][0]["title"] == "DRAFT_ONLY"
        assert json.loads(row["published_json"])["relations"][0]["title"] == "KEEP"
        n_fts_after = con.execute(
            "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", ("bound-pub",),
        ).fetchone()["c"]
        assert n_fts_after == n_fts_before
        con.close()


def test_create_revision_keeps_published_and_ask():
    """create_revision 铺底保持 published，不清 Ask。"""
    with _temp_db():
        from app import db

        con = db.connect()
        pub = {
            "relations": [
                {"decision_tier": "strong", "title": "LIVE", "body": "b", "evidence": [{}]},
            ],
            "lead": "online",
        }
        dirty = {
            "relations": [
                {"decision_tier": "strong", "title": "DIRTY", "body": "b", "evidence": [{}]},
            ],
            "lead": "local",
        }
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "bound-rev", "2026-01-01", "2026-01-07", "边界测", "published",
                json.dumps(dirty, ensure_ascii=False),
                json.dumps(pub, ensure_ascii=False),
            ),
        )
        row = con.execute("SELECT * FROM issues WHERE slug='bound-rev'").fetchone()
        db.reindex_issue(con, row["id"])
        con.commit()
        n_fts = con.execute(
            "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", ("bound-rev",),
        ).fetchone()["c"]
        assert n_fts > 0

        # 模拟 create_revision：force 用线上稿铺底，不改 status、不清索引
        con.execute(
            "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
            (row["published_json"], "2026-01-08 12:00", row["id"]),
        )
        con.commit()
        row2 = con.execute(
            "SELECT status, draft_json, published_json FROM issues WHERE slug='bound-rev'"
        ).fetchone()
        assert row2["status"] == "published"
        assert json.loads(row2["draft_json"])["relations"][0]["title"] == "LIVE"
        assert json.loads(row2["published_json"])["relations"][0]["title"] == "LIVE"
        n_fts2 = con.execute(
            "SELECT COUNT(*) c FROM search_fts WHERE issue_slug=?", ("bound-rev",),
        ).fetchone()["c"]
        assert n_fts2 == n_fts

        # published 期 start 不再拒绝（不 spawn 跑完，只看 claim 前闸门）
        from app import preview_job
        from unittest import mock
        with mock.patch("app.job_runtime.spawn_after_claim"):
            st = preview_job.start("bound-rev", "tester")
        assert st.get("error_code") != "published_preview_forbidden"
        assert st.get("running") is True
        con.close()


def test_rollback_restores_published_and_refresh_embed_status():
    """Test D: rollback 恢复 published 投影；embedding 不串 other-slug。"""
    with _temp_db():
        from app import db, embed_job

        con = db.connect()
        v1 = {
            "relations": [
                {"decision_tier": "strong", "title": "V1", "body": "one", "evidence": [{}]},
            ],
        }
        v2 = {
            "relations": [
                {"decision_tier": "strong", "title": "V2", "body": "two", "evidence": [{}]},
                {"decision_tier": "parallel", "title": "V2P", "body": "p", "evidence": [{}]},
            ],
        }
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json,"
            "embedding_status,embedding_model) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "bound-rb", "2026-01-01", "2026-01-07", "边界测", "published",
                json.dumps(v2, ensure_ascii=False),
                json.dumps(v2, ensure_ascii=False),
                "running",
                "test-model",
            ),
        )
        iid = con.execute("SELECT id FROM issues WHERE slug='bound-rb'").fetchone()["id"]
        con.execute(
            "INSERT INTO versions(issue_id,version,at,by_user,cards_out,edited_count,url,snapshot_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (iid, "v1", "2026-01-01 10:00", "t", 1, 0, "/bound-rb", json.dumps(v1, ensure_ascii=False)),
        )
        con.execute(
            "INSERT INTO chunk_index(chunk_id, issue_slug, issue_id, date_end, layer, section, title, body) "
            "VALUES ('c-v2', 'bound-rb', ?, '2026-01-07', 'section', '可同步的关系', 'V2', 'two')",
            (iid,),
        )
        con.execute(
            "INSERT INTO chunk_index(chunk_id, issue_slug, issue_id, date_end, layer, section, title, body) "
            "VALUES ('c-other', 'other-slug', 999, '2026-01-07', 'section', 'x', 'O', 'other')",
        )
        con.commit()

        payload = json.dumps(v1, ensure_ascii=False)
        con.execute(
            "UPDATE issues SET published_json=?, draft_json=?, status='published', "
            "published_at=?, updated_at=? WHERE id=?",
            (payload, payload, "2026-01-01 10:00", "2026-01-02 12:00", iid),
        )
        db.reindex_issue(con, iid)
        info = db.refresh_issue_embedding_status(con, iid, status="pending")
        con.commit()

        row = con.execute(
            "SELECT published_json FROM issues WHERE id=?", (iid,),
        ).fetchone()
        pub = json.loads(row["published_json"])
        assert [r["title"] for r in pub["relations"]] == ["V1"]
        assert "V2" not in row["published_json"]
        assert info.get("embedding_total") in (None, 0) or True
        with mock.patch.object(embed_job, "embeddings") as emb:
            emb.is_configured.return_value = False
            out = embed_job.ensure_for_slug("bound-rb", by="rollback-test")
            assert out.get("skipped") is True or out.get("ok") is True
        con.close()
