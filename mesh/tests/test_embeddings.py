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
    # legacy 融合：score 越小越好
    import os
    prev = os.environ.get("MESH_FUSION")
    os.environ["MESH_FUSION"] = "legacy"
    try:
        ranked = retriever.rerank_hits(hits, "面壁智能", limit=2)
    finally:
        if prev is None:
            os.environ.pop("MESH_FUSION", None)
        else:
            os.environ["MESH_FUSION"] = prev
    assert ranked[0]["title"] == "面壁智能"


def test_merge_hits_rrf_scale_immune():
    """RRF 只用通道内位次：词法小整数 vs 向量 -cosine 的量纲差异不再影响融合。"""
    fts = [
        {"issue_slug": "a", "section": "记录", "title": "t1", "body": "b1", "score": -9.0, "source": "fts"},
        {"issue_slug": "a", "section": "记录", "title": "t2", "body": "b2", "score": -2.0, "source": "fts"},
    ]
    vec = [
        {"issue_slug": "a", "section": "记录", "title": "t3", "body": "b3", "score": -0.71, "source": "vector", "chunk_id": "c3"},
        {"issue_slug": "a", "section": "记录", "title": "t4", "body": "b4", "score": -0.66, "source": "vector", "chunk_id": "c4"},
    ]
    merged = search.merge_hits(fts, vec, limit=10)
    # 两通道各取 rank1：RRF 分数应相等（量纲不可比 → 只看位次）
    top2 = {h["title"] for h in merged[:2]}
    assert top2 == {"t1", "t3"}
    assert abs(float(merged[0]["score"]) - float(merged[1]["score"])) < 1e-9


def test_merge_hits_rrf_consensus_beats_single_channel():
    """同一文档被多路召回 → 贡献累加，胜过只被单路召回的同位次文档。"""
    shared = "面壁智能与编辑部沟通端云协同方案落地细节"
    a = [
        {"issue_slug": "a", "section": "记录", "title": "both", "body": shared, "score": -5.0, "source": "fts"},
        {"issue_slug": "a", "section": "记录", "title": "fts_only", "body": "完全无关的另外一段内容描述", "score": -1.0, "source": "fts"},
    ]
    b = [
        {"issue_slug": "a", "section": "记录", "title": "both", "body": shared, "score": -0.9, "source": "vector", "chunk_id": "cx"},
    ]
    merged = search.merge_hits(a, b, limit=10)
    # 共识文档（两路各命中）应排在最前，且分数 = 两路贡献之和
    assert merged[0]["title"] == "both"
    assert float(merged[0]["_rrf_score"]) > float(merged[1]["_rrf_score"])
    assert set(merged[0]["_rrf_ranks"].keys()) == {"g0", "g1"}


def test_merge_hits_legacy_still_available():
    """legacy 融合保留（A/B 对照）：仍按原始分比较。"""
    fts = [{"issue_slug": "a", "section": "记录", "title": "t1", "body": "b", "score": -8.0, "source": "fts"}]
    weak = [{"issue_slug": "a", "section": "记录", "title": "t1", "body": "b", "score": -2.0, "source": "fts"}]
    merged = search.merge_hits_legacy(fts, weak, limit=1)
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
