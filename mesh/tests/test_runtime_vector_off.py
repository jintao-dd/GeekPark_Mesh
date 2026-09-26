"""Runtime：Vector OFF 时 prepare 不得调用 embed_one。"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_prepare_lexical_skips_embed_when_vector_off(monkeypatch):
    monkeypatch.delenv("MESH_EMBED_ENABLED", raising=False)
    monkeypatch.delenv("MESH_VECTOR_ENABLED", raising=False)
    # 即使 key 在，默认 OFF 也不得 embed
    monkeypatch.setenv("MESH_EMBED_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("MESH_EMBED_API_KEY", "sk-test")

    from app import embeddings, ask_engine
    from app.ask_scope import AskScope

    assert embeddings.retrieval_execution_mode(structured_candidate=False) == "lexical"

    scope = AskScope(
        channel="harness",
        slug="2026-8-17",
        team="编辑部",
        user_id=None,
        feishu_open_id="ou_x",
        role="viewer",
        user_team="编辑部",
    )

    with mock.patch.object(embeddings, "embed_one", side_effect=AssertionError("embed must not run")):
        with mock.patch("app.retriever.retrieve") as ret:
            ret.return_value = (
                "lexical",
                [],
                {"date_from": None, "date_to": None, "used_vector": False},
            )
            # guard may short-circuit empty-ish; use a normal query
            with mock.patch("app.ask_query_guard.guard_direct_answer", return_value=None):
                with mock.patch("app.ask_planner.plan_retrieval") as plan:
                    from types import SimpleNamespace

                    plan.return_value = SimpleNamespace(
                        path="hybrid",
                        set_op="none",
                        confidence=0.0,
                        intent=None,
                        fallback_note="",
                    )
                    con = mock.MagicMock()
                    out = ask_engine.prepare(con, "编辑部关注了哪些话题", scope)
    assert out.get("retrieval_execution_mode") == "lexical"
    # retrieve 收到的 query_vec 必须是 None
    assert ret.call_args is not None
    kwargs = ret.call_args.kwargs if ret.call_args.kwargs else {}
    # retrieve(con, sq, scope, intent=..., query_vec=...)
    if "query_vec" in kwargs:
        assert kwargs["query_vec"] is None
    else:
        # positional: con, sq, scope — query_vec is kw-only in signature with default
        assert ret.call_args[1].get("query_vec") is None
