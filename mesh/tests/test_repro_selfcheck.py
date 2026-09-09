"""Environment self-check hard gates (no new quality version)."""
from __future__ import annotations

import os

import pytest


def test_sha_mismatch_fails(monkeypatch):
    monkeypatch.setenv("MESH_BUILD_GIT_SHA", "aaaaaaaaaaaa")
    monkeypatch.setenv("MESH_EXPECTED_GIT_SHA", "bbbbbbbbbbbb")
    monkeypatch.setenv("MESH_IMAGE_TAG", "2026-09-09-aaaaaaaaaaaa")
    monkeypatch.setenv("MESH_EMBED_ENABLED", "0")
    monkeypatch.setenv("MESH_VECTOR_ENABLED", "0")

    from app import embeddings, repro_selfcheck

    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    doc = repro_selfcheck.run_selfcheck(probe_embed=False)
    assert doc["REPRO_STATUS"] == "FAIL"
    assert any("expected_sha" in f for f in doc["failures"])


def test_vector_off_embed_calls_fails(monkeypatch):
    monkeypatch.setenv("MESH_BUILD_GIT_SHA", "cccccccccccc")
    monkeypatch.setenv("MESH_EXPECTED_GIT_SHA", "cccccccccccc")
    monkeypatch.setenv("MESH_IMAGE_TAG", "2026-09-09-cccccccccccc")
    monkeypatch.setenv("MESH_EMBED_ENABLED", "0")
    monkeypatch.setenv("MESH_VECTOR_ENABLED", "0")

    from app import embeddings, repro_selfcheck

    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    monkeypatch.setattr(repro_selfcheck, "_probe_embed_on_prepare", lambda: 2)
    doc = repro_selfcheck.run_selfcheck(probe_embed=True)
    assert doc["REPRO_STATUS"] == "FAIL"
    assert any("embedding_calls" in f for f in doc["failures"])


def test_pass_when_aligned(monkeypatch):
    monkeypatch.setenv("MESH_BUILD_GIT_SHA", "dddddddddddd")
    monkeypatch.setenv("MESH_EXPECTED_GIT_SHA", "dddddddddddd")
    monkeypatch.setenv("MESH_IMAGE_TAG", "2026-09-09-dddddddddddd")
    monkeypatch.setenv("MESH_EMBED_ENABLED", "0")
    monkeypatch.setenv("MESH_VECTOR_ENABLED", "0")
    monkeypatch.setenv("MESH_LLM_MODEL_ANSWER", "claude-sonnet-4-6")
    monkeypatch.setenv("MESH_LLM_MODEL", "claude-sonnet-4-6")

    from app import embeddings, repro_selfcheck
    import app.llm as llm_mod

    monkeypatch.setattr(embeddings, "enabled", lambda: False)
    monkeypatch.setattr(repro_selfcheck, "_probe_embed_on_prepare", lambda: 0)
    monkeypatch.setattr(
        llm_mod,
        "model_for_task",
        lambda task: "claude-sonnet-4-6",
    )
    doc = repro_selfcheck.run_selfcheck(probe_embed=True)
    assert doc["REPRO_STATUS"] == "PASS"
    assert doc["environment_manifest"]["vector"] == "OFF"
    assert repro_selfcheck.is_pass()


def test_embed_call_count_increments_even_when_off(monkeypatch):
    monkeypatch.setenv("MESH_EMBED_ENABLED", "0")
    monkeypatch.setenv("MESH_VECTOR_ENABLED", "0")
    from app import embeddings

    embeddings.reset_call_count()
    assert embeddings.enabled() is False
    embeddings.embed_one("x")
    assert embeddings.call_count() == 1
