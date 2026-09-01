"""成文话术清洗 + analyses 表。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_analysis, ask_store, db


def test_scrub_user_answer():
    assert ask_analysis._scrub_user_answer("根据已校验断言，编辑部接触了甲。").startswith("编辑部")
    assert "断言" not in ask_analysis._scrub_user_answer("已校验断言：某某")
    assert ask_analysis._scrub_user_answer("正常回答。") == "正常回答。"


def test_ask_store_roundtrip():
    con = db.connect()
    db.migrate(con)
    try:
        ask_store.ensure_table(con)
        ask_store.finish(
            con,
            analysis_id="test-aid-1",
            answer="答",
            context_refs={"analysis_id": "test-aid-1", "entities": ["甲"]},
            usage={"llm_calls": 1},
            status="completed",
        )
        db.commit_retry(con)
        row = ask_store.get(con, "test-aid-1")
        assert row and row["answer"] == "答"
        assert row["context_refs"]["entities"] == ["甲"]
    finally:
        try:
            con.execute("DELETE FROM ask_analyses WHERE analysis_id=?", ("test-aid-1",))
            db.commit_retry(con)
        except Exception:
            pass
        con.close()
