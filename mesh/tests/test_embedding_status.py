"""Per-issue embedding 状态与按 slug 增量补向量。"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chunk_index, db, embed_job, job_store


def _seed_issue(con, iid=88888, slug="test-embed"):
    con.execute(
        "INSERT OR IGNORE INTO issues(id,slug,date_start,date_end,period_label,status,published_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (iid, slug, "2026-01-01", "2026-01-07", "测试", "published", "{}"),
    )
    con.execute("DELETE FROM chunk_index")
    con.execute("DELETE FROM chunk_embeddings")
    con.execute(
        "INSERT INTO chunk_index(chunk_id, issue_slug, issue_id, date_end, layer, section, title, body) "
        "VALUES ('c1', ?, ?, '2026-01-07', 'section', '可同步的关系', 'A', 'body one')",
        (slug, iid),
    )
    con.execute(
        "INSERT INTO chunk_index(chunk_id, issue_slug, issue_id, date_end, layer, section, title, body) "
        "VALUES ('c2', ?, ?, '2026-01-07', 'section', '可同步的关系', 'B', 'body two')",
        (slug, iid),
    )
    con.execute(
        "INSERT INTO chunk_index(chunk_id, issue_slug, issue_id, date_end, layer, section, title, body) "
        "VALUES ('other', 'other-slug', 1, '2026-01-07', 'section', 'x', 'C', 'other')",
    )
    con.commit()


def test_rebuild_issue_does_not_sync_embed():
    con = db.connect()
    db.migrate(con)
    try:
        with mock.patch.object(chunk_index, "embed_all_missing") as emb:
            chunk_index.rebuild_issue(con, 999999)
            emb.assert_not_called()
    finally:
        con.close()


def test_refresh_issue_embedding_status_pending():
    con = db.connect()
    db.migrate(con)
    try:
        _seed_issue(con)
        with mock.patch("app.embeddings.is_configured", return_value=True), mock.patch(
            "app.embeddings.model_name", return_value="test-model",
        ):
            info = db.refresh_issue_embedding_status(con, 88888)
        assert info["embedding_status"] == "pending"
        assert info["embedding_total"] == 2
        assert info["embedding_done"] == 0
        assert info["embedding_model"] == "test-model"
    finally:
        con.execute("DELETE FROM chunk_index WHERE issue_slug='test-embed'")
        con.execute("DELETE FROM issues WHERE id=88888")
        con.commit()
        con.close()


def test_refresh_status_after_model_change():
    con = db.connect()
    db.migrate(con)
    try:
        _seed_issue(con)
        con.execute(
            "UPDATE issues SET embedding_status='completed', embedding_total=2, embedding_done=2, "
            "embedding_model='model-a' WHERE id=88888"
        )
        con.execute(
            "INSERT INTO chunk_embeddings(chunk_id, model, dim, vector_json) VALUES ('c1','model-a',2,'[0.1,0.2]')"
        )
        con.execute(
            "INSERT INTO chunk_embeddings(chunk_id, model, dim, vector_json) VALUES ('c2','model-a',2,'[0.1,0.2]')"
        )
        con.commit()
        with mock.patch("app.embeddings.is_configured", return_value=True), mock.patch(
            "app.embeddings.model_name", return_value="model-b",
        ):
            info = db.refresh_issue_embedding_status(con, 88888)
        assert info["embedding_status"] == "pending"
        assert info["embedding_done"] == 0
        assert info["embedding_total"] == 2
        assert info["embedding_model"] == "model-b"
    finally:
        con.execute("DELETE FROM chunk_embeddings")
        con.execute("DELETE FROM chunk_index")
        con.execute("DELETE FROM issues WHERE id=88888")
        con.commit()
        con.close()


def test_embed_missing_for_slug_only_target():
    con = db.connect()
    db.migrate(con)
    try:
        _seed_issue(con)

        def fake_embed(texts):
            return [[0.1, 0.2] for _ in texts]

        with mock.patch("app.embeddings.is_configured", return_value=True), mock.patch(
            "app.embeddings.model_name", return_value="test-model",
        ), mock.patch("app.embeddings.batch_size", return_value=8), mock.patch(
            "app.embeddings.embed_texts", side_effect=fake_embed,
        ):
            n = chunk_index.embed_missing_for_slug(con, "test-embed")
            assert n == 2
            other = con.execute(
                """SELECT COUNT(*) c FROM chunk_index c
                   LEFT JOIN chunk_embeddings e ON e.chunk_id=c.chunk_id
                   WHERE c.issue_slug='other-slug' AND e.chunk_id IS NOT NULL"""
            ).fetchone()["c"]
            assert other == 0
            info = db.refresh_issue_embedding_status(con, 88888)
            assert info["embedding_status"] == "completed"
            assert info["embedding_done"] == 2
    finally:
        con.execute("DELETE FROM chunk_embeddings")
        con.execute("DELETE FROM chunk_index WHERE issue_slug IN ('test-embed','other-slug')")
        con.execute("DELETE FROM issues WHERE id=88888")
        con.commit()
        con.close()


def test_ensure_for_slug_idempotent():
    con = db.connect()
    db.migrate(con)
    slug = "test-embed-idem"
    try:
        _seed_issue(con, iid=88889, slug=slug)
        with mock.patch("app.embeddings.is_configured", return_value=True), mock.patch(
            "app.embeddings.model_name", return_value="test-model",
        ), mock.patch("app.job_runtime.spawn_after_claim") as spawn:
            first = embed_job.ensure_for_slug(slug, by="test")
            second = embed_job.ensure_for_slug(slug, by="test")
        assert first.get("queued") is True
        assert first.get("idempotent") is False
        assert second.get("idempotent") is True
        assert second.get("already_running") is True
        assert spawn.call_count == 1
        st = job_store.get(embed_job.KIND, slug, embed_job._defaults(slug))
        assert st.get("running") is True
    finally:
        job_store.drop(embed_job.KIND, slug)
        con.execute("DELETE FROM chunk_index")
        con.execute("DELETE FROM issues WHERE id=88889")
        con.commit()
        con.close()
