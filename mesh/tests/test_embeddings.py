"""embedding / 检索分数逻辑单元测试（无网络）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import embeddings, retriever, search


def test_cosine_identical():
    a = [1.0, 0.0, 0.0]
    assert embeddings.cosine(a, a) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert embeddings.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_expand_query_alias():
    out = embeddings.expand_query("embodied robot")
    assert "具身智能" in " ".join(out)


def test_rerank_prefers_title_match():
    hits = [
        {"title": "无关", "body": "x", "score": -0.1, "source": "fts"},
        {"title": "面壁智能", "body": "y", "score": -0.05, "source": "fts"},
    ]
    ranked = retriever.rerank_hits(hits, "面壁智能", limit=2)
    assert ranked[0]["title"] == "面壁智能"


def test_merge_hits_prefers_better_bm25():
    fts = [{"issue_slug": "a", "section": "记录", "title": "t1", "body": "b", "score": -8.0, "source": "fts"}]
    weak = [{"issue_slug": "a", "section": "记录", "title": "t1", "body": "b", "score": -2.0, "source": "fts"}]
    merged = search.merge_hits(fts, weak, limit=1)
    assert float(merged[0]["score"]) < float(weak[0]["score"])


def test_is_configured_requires_key_when_base_set(monkeypatch):
    monkeypatch.delenv("MESH_EMBED_API_KEY", raising=False)
    monkeypatch.delenv("MESH_LLM_API_KEY", raising=False)
    monkeypatch.setenv("MESH_EMBED_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("MESH_EMBED_ENABLED", "1")
    assert embeddings.is_configured() is False
    monkeypatch.setenv("MESH_EMBED_API_KEY", "sk-test")
    assert embeddings.is_configured() is True


def test_vector_default_off_even_if_keys_present(monkeypatch):
    """冻结：Vector OFF 默认；仅有 key 不得进热路径 embed。"""
    monkeypatch.delenv("MESH_EMBED_ENABLED", raising=False)
    monkeypatch.delenv("MESH_VECTOR_ENABLED", raising=False)
    monkeypatch.setenv("MESH_EMBED_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("MESH_EMBED_API_KEY", "sk-test")
    assert embeddings.enabled() is False
    assert embeddings.is_configured() is False
    assert embeddings.retrieval_execution_mode(structured_candidate=False) == "lexical"


def test_vector_on_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("MESH_EMBED_ENABLED", "1")
    monkeypatch.setenv("MESH_EMBED_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("MESH_EMBED_API_KEY", "sk-test")
    assert embeddings.enabled() is True
    assert embeddings.retrieval_execution_mode(structured_candidate=False) == "hybrid"
    assert embeddings.retrieval_execution_mode(structured_candidate=True) == "structured"


def test_base_urls_supports_comma_fallback(monkeypatch):
    monkeypatch.setenv(
        "MESH_EMBED_BASE_URL",
        "https://kspmas.ksyun.com/v1,https://kapws.ksyun.com/v1",
    )
    assert embeddings._base_urls() == [
        "https://kspmas.ksyun.com/v1",
        "https://kapws.ksyun.com/v1",
    ]
