"""后台模型清单 / 任务切换（model_settings + llm.model_for_task DB 覆盖）单测。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def model_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app import db, db_conn

    monkeypatch.setattr(db_conn, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(seed=False)
    # 保证不误连 PG
    monkeypatch.setattr(db_conn, "MESH_DB_URL", "")
    monkeypatch.setenv("MESH_LLM_MODEL", "anthropic/claude-4.8-opus")
    monkeypatch.setenv("MESH_LLM_MODEL_ANSWER", "anthropic/claude-4.8-opus")
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def test_env_default_when_no_db_choice(model_db):
    from app import db, model_settings

    con = db.connect()
    try:
        assert model_settings.get_task_choice(con, "answer") == ""
        assert model_settings.effective_model(con, "answer") == "anthropic/claude-4.8-opus"
    finally:
        con.close()


def test_add_list_delete_model(model_db):
    from app import db, model_settings

    con = db.connect()
    try:
        model_settings.ensure_catalog(con)
        db.commit_retry(con)
        # 首次 ensure：把 env 兜底固化进清单
        assert "anthropic/claude-4.8-opus" in model_settings.list_models(con)

        ok, _ = model_settings.add_model(con, "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)
        assert ok
        assert "deepseek/deepseek-v4.1-flash" in model_settings.list_models(con)

        # 重复添加
        ok2, msg = model_settings.add_model(con, "deepseek/deepseek-v4.1-flash")
        assert not ok2 and "已在清单" in msg

        # 非法 id
        ok3, msg3 = model_settings.add_model(con, "bad model!!")
        assert not ok3 and "不合法" in msg3

        ok4, _ = model_settings.delete_model(con, "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)
        assert ok4
        assert "deepseek/deepseek-v4.1-flash" not in model_settings.list_models(con)
    finally:
        con.close()


def test_set_task_choice_and_effective(model_db):
    from app import db, model_settings

    con = db.connect()
    try:
        model_settings.add_model(con, "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)

        ok, _ = model_settings.set_task_choice(con, "answer", "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)
        assert ok
        assert model_settings.effective_model(con, "answer") == "deepseek/deepseek-v4.1-flash"
        # 其它任务不受影响
        assert model_settings.effective_model(con, "semantic") == "anthropic/claude-4.8-opus"

        # 清空 = 回落 env
        ok2, _ = model_settings.set_task_choice(con, "answer", "")
        db.commit_retry(con)
        assert ok2
        assert model_settings.effective_model(con, "answer") == "anthropic/claude-4.8-opus"

        # 选一个不在清单里的模型 → 拒绝
        ok3, msg3 = model_settings.set_task_choice(con, "answer", "ghost/model")
        assert not ok3 and "不在清单" in msg3
    finally:
        con.close()


def test_delete_model_clears_task_choice(model_db):
    from app import db, model_settings

    con = db.connect()
    try:
        model_settings.add_model(con, "deepseek/deepseek-v4.1-flash")
        model_settings.set_task_choice(con, "answer", "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)
        model_settings.delete_model(con, "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)
        assert model_settings.get_task_choice(con, "answer") == ""
        assert model_settings.effective_model(con, "answer") == "anthropic/claude-4.8-opus"
    finally:
        con.close()


def test_model_for_task_reads_db_choice(model_db):
    """llm.model_for_task 必须能读到 DB 选择（后台切换即时生效）。"""
    from app import db, llm, model_settings

    llm.model_settings_cache_bust()

    con = db.connect()
    try:
        model_settings.add_model(con, "deepseek/deepseek-v4.1-flash")
        model_settings.set_task_choice(con, "answer", "deepseek/deepseek-v4.1-flash")
        db.commit_retry(con)
    finally:
        con.close()

    llm.model_settings_cache_bust()
    assert llm.model_for_task("answer") == "deepseek/deepseek-v4.1-flash"
    # 未覆盖的任务仍走 env
    assert llm.model_for_task("default") == "anthropic/claude-4.8-opus"
