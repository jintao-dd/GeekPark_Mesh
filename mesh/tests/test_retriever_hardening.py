"""Retrieve 硬化：去重、团队 enrich、slug。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import search
from app.retriever import _hit_team_allowed, _enrich_owner_team, rerank_hits


def test_fts_digest_allowed_under_team_filter():
    hit = {"source": "fts", "section": "接触过的人和公司", "title": "某公司", "body": "x"}
    assert _hit_team_allowed(hit, "编辑部")


def test_merge_dedup_fts_and_vector_same_body():
    body = "面壁智能与编辑部沟通端云协同方案"
    fts = [{
        "issue_slug": "2026-8-17", "section": "接触过的人和公司", "title": "面壁",
        "body": body, "score": -0.5, "source": "fts",
    }]
    vec = [{
        "issue_slug": "2026-8-17", "section": "抽取条目", "title": "面壁",
        "body": body, "score": -0.3, "source": "vector", "chunk_id": "abc123",
    }]
    merged = search.merge_hits(fts, vec, limit=5)
    assert len(merged) == 1


def test_seed_boost_requires_overlap():
    hits = [
        {"title": "无关", "body": "完全无关内容", "score": 0.0, "chunk_id": "seed-1"},
        {"title": "面壁智能", "body": "面壁智能进展", "score": 0.0, "chunk_id": "other"},
    ]
    ranked = rerank_hits(hits, "面壁智能", limit=2, seed_chunk_ids=["seed-1"])
    assert ranked[0]["title"] == "面壁智能"


def test_enrich_data_source_team():
    class FakeCon:
        def execute(self, *a, **k):
            class R:
                def fetchone(self):
                    return None
            return R()

    h = _enrich_owner_team(FakeCon(), {"section": "Data Source", "title": "编辑部", "body": "贡献"})
    assert h.get("owner_team") == "编辑部"
