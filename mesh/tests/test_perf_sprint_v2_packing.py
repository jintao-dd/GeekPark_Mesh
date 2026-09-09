"""Performance Sprint v2 · packing limits (no quality version)."""
from __future__ import annotations


def test_pack_answer_contexts_dedupes_and_trims(monkeypatch):
    monkeypatch.setenv("MESH_ANSWER_CTX_N", "4")
    monkeypatch.setenv("MESH_ANSWER_BODY_CHARS", "40")
    from app import llm

    ctxs = [
        {"期号": "2026-8-17", "章节": "A", "标题": "T1", "内容": "x" * 200, "chunk_id": 1},
        {"期号": "2026-8-17", "章节": "A", "标题": "T1", "内容": "x" * 200},  # dup
        {"期号": "2026-8-17", "章节": "检索说明", "标题": "meta", "内容": "skip"},
        {"期号": "2026-8-10", "章节": "B", "标题": "T2", "内容": "y" * 100},
        {"期号": "2026-8-03", "章节": "C", "标题": "T3", "内容": "z" * 100},
        {"期号": "2026-7-27", "章节": "D", "标题": "T4", "内容": "w" * 100},
        {"期号": "2026-7-20", "章节": "E", "标题": "T5", "内容": "v" * 100},
    ]
    out = llm.pack_answer_contexts(ctxs)
    assert len(out) == 4
    assert all("chunk_id" not in c for c in out)
    assert all(len(c["内容"]) <= 40 for c in out)
    assert out[0]["标题"] == "T1"


def test_semantic_snip_limits(monkeypatch):
    monkeypatch.setenv("MESH_SEMANTIC_SNIP_N", "3")
    monkeypatch.setenv("MESH_SEMANTIC_SNIP_CHARS", "20")
    from app.agent import claim_semantic_ext as se

    ctxs = [{"标题": f"t{i}", "内容": "正文" * 40} for i in range(10)]
    text = se._ctx_snip(ctxs)
    assert text.count("[") == 3
    for line in text.split("\n\n"):
        body = line.split("\n", 1)[-1]
        assert len(body) <= 20


def test_compose_support_unchanged():
    from app.agent.claim_semantic_ext import compose_support

    assert compose_support("strong", "entailing", False) == "supported"
    assert compose_support("strong", "direct_related", False) == "insufficient"
    assert compose_support("weak", "topical", False) == "supported"
    assert compose_support("strong", "entailing", True) == "contradicted"
