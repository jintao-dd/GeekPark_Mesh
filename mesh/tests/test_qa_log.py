"""飞书/Agent 回答质量采集：落库、flag 派生、筛选、人工标注、来源隔离。

覆盖：证据与 answer_status 落库、无证据标记、状态筛选、人工标差、
以及**来源隔离**（真实飞书默认落库；评测/HTTP 默认不落，防自污染）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, qa_log


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """默认清掉采集开关，避免外部环境串味。"""
    monkeypatch.delenv("MESH_QA_LOG", raising=False)
    monkeypatch.delenv("MESH_QA_LOG_EVAL", raising=False)
    yield


def _reset(con):
    con.execute("DELETE FROM agent_qa_log")
    con.commit()


def _answer(**kw) -> dict:
    base = {
        "text": "面壁智能本周在编辑部出现。",
        "display_text": "面壁智能本周在编辑部出现。（来源：2026-09-15）",
        "intent": "fact_lookup",
        "tools_called": ["ask"],
        "evidence_refs": ["item:101"],
        "claim_bindings": [{"claim": "面壁智能", "status": "grounded", "evidence_refs": ["item:101"]}],
        "trace": {"conversation_route": "ask", "n_hits": 3, "timings": {"retrieve_ms": 42}},
        "refused": False,
    }
    base.update(kw)
    return base


def test_record_and_read_back():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        rid = qa_log.record_answer(
            con,
            envelope={"channel": "feishu_dm", "feishu_open_id": "ou_abc", "chat_id": "oc_x"},
            answer=_answer(),
            question="面壁智能这周有什么动静",
            latency_ms=1234,
            request_id="req1",
        )
        con.commit()
        assert rid
        t = qa_log.get_turn(con, rid)
        assert t["answer_status"] == "grounded"
        assert t["evidence_count"] == 1
        assert t["evidence_refs"] == ["item:101"]
        assert t["claim_bindings"][0]["status"] == "grounded"
        assert t["channel"] == "feishu_dm"
        assert t["source"] == "feishu"
        assert t["route"] == "ask"
        assert t["trace_json"]["n_hits"] == 3
        assert t["flag"] == ""
    finally:
        con.close()


def test_no_evidence_flagged():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        rid = qa_log.record_answer(
            con,
            envelope={"channel": "feishu_group"},
            answer=_answer(evidence_refs=[], claim_bindings=[], trace={}),
            question="随便问问",
            latency_ms=100,
        )
        con.commit()
        t = qa_log.get_turn(con, rid)
        assert t["answer_status"] == "unknown"
        assert t["flag"] == "no_evidence"
    finally:
        con.close()


def test_unsupported_and_weak_flags():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        r1 = qa_log.record_answer(
            con, envelope={"channel": "feishu_dm"},
            answer=_answer(claim_bindings=[{"claim": "x", "status": "unsupported"}]),
            question="q1",
        )
        r2 = qa_log.record_answer(
            con, envelope={"channel": "feishu_dm"},
            answer=_answer(claim_bindings=[{"claim": "x", "status": "weak"}]),
            question="q2",
        )
        con.commit()
        assert qa_log.get_turn(con, r1)["flag"] == "unsupported"
        assert qa_log.get_turn(con, r2)["flag"] == "weak"
    finally:
        con.close()


def test_list_filters_and_feedback():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        good = qa_log.record_answer(
            con, envelope={"channel": "feishu_dm"}, answer=_answer(), question="a"
        )
        bad = qa_log.record_answer(
            con, envelope={"channel": "feishu_group"},
            answer=_answer(claim_bindings=[{"claim": "x", "status": "unsupported"}]),
            question="b",
        )
        con.commit()

        page = qa_log.list_turns(con, channel="feishu_dm")
        assert page["total"] == 1 and page["turns"][0]["id"] == good

        page = qa_log.list_turns(con, answer_status="grounded")
        assert page["total"] == 1

        assert qa_log.set_feedback(con, bad, feedback="bad", note="答错了", by="tester")
        con.commit()
        page = qa_log.list_turns(con, only_bad=True)
        assert page["total"] == 1 and page["turns"][0]["id"] == bad
        assert page["turns"][0]["feedback"] == "bad"
        assert page["turns"][0]["feedback_note"] == "答错了"

        assert qa_log.set_feedback(con, bad, feedback="nonsense") is False
        con.commit()
        assert qa_log.list_turns(con, only_bad=True)["total"] == 1  # 未清除
    finally:
        con.close()


def test_record_never_raises_on_bad_input():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        # answer 为怪类型也不应抛（用真实飞书渠道，确保走到 INSERT）
        assert qa_log.record_answer(con, envelope={"channel": "feishu_dm"}, answer={}, question="") is not None
        con.commit()
    finally:
        con.close()


def test_stats_shape():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        qa_log.record_answer(con, envelope={"channel": "feishu_dm"}, answer=_answer(), question="a")
        con.commit()
        s = qa_log.stats(con, days=7)
        assert s["total"] >= 1
        assert "grounded" in s["by_status"]
        assert s["by_channel"].get("feishu_dm", 0) >= 1
        assert s["by_source"].get("feishu", 0) >= 1
    finally:
        con.close()


# ---------- 来源隔离（防自污染）----------


def test_should_record_matrix(monkeypatch):
    # 默认：真实飞书落库，评测/HTTP 不落
    assert qa_log.should_record("feishu_dm") is True
    assert qa_log.should_record("feishu_group") is True
    assert qa_log.should_record("harness") is False
    assert qa_log.should_record("web") is False
    assert qa_log.should_record("") is False
    # 显式开评测采集
    monkeypatch.setenv("MESH_QA_LOG_EVAL", "1")
    assert qa_log.should_record("harness") is True
    assert qa_log.should_record("web") is True
    # 全局关
    monkeypatch.setenv("MESH_QA_LOG", "0")
    assert qa_log.should_record("feishu_dm") is False
    assert qa_log.should_record("harness") is False


def test_eval_channel_not_recorded_by_default():
    """核心：评测/HTTP 调用不得写进真实样本库。"""
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        assert qa_log.record_answer(con, envelope={"channel": "harness"}, answer=_answer(), question="评测题") is None
        assert qa_log.record_answer(con, envelope={"channel": "web"}, answer=_answer(), question="http 题") is None
        con.commit()
        assert qa_log.list_turns(con)["total"] == 0
    finally:
        con.close()


def test_eval_recorded_when_enabled_and_tagged(monkeypatch):
    monkeypatch.setenv("MESH_QA_LOG_EVAL", "1")
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        rid = qa_log.record_answer(con, envelope={"channel": "harness"}, answer=_answer(), question="评测题")
        con.commit()
        t = qa_log.get_turn(con, rid)
        assert t["source"] == "eval"
        # 默认只看飞书时，评测样本不出现
        assert qa_log.list_turns(con, source="feishu")["total"] == 0
        assert qa_log.list_turns(con, source="eval")["total"] == 1
    finally:
        con.close()


def test_feishu_tagged_as_feishu_source():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        rid = qa_log.record_answer(con, envelope={"channel": "feishu_group"}, answer=_answer(), question="真实题")
        con.commit()
        t = qa_log.get_turn(con, rid)
        assert t["source"] == "feishu"
        assert qa_log.list_turns(con, source="feishu")["total"] == 1
    finally:
        con.close()


def test_legacy_table_without_source_column_is_migrated_and_backfilled():
    """老库没有 source 列：ensure_schema 必须补上并回填，且不得毒化事务。

    线上 500 的根因：建 source 索引发生在补列之前，PG 报「column does not exist」
    后事务被 abort，随后的 ALTER TABLE ADD COLUMN 又被 except 吞掉，
    于是 source 永远补不上，/admin/qa 每次查询都撞 InFailedSqlTransaction。
    """
    con = db.connect()
    try:
        con.execute("DROP TABLE IF EXISTS agent_qa_log")
        # 造一个缺 source 的旧表（与 bfde11a 的 schema 一致）
        con.execute(
            """CREATE TABLE agent_qa_log(
              id INTEGER PRIMARY KEY, request_id TEXT, channel TEXT,
              feishu_open_id TEXT, mesh_user_id INTEGER, session_id TEXT, chat_id TEXT,
              question TEXT NOT NULL, answer_text TEXT, intent TEXT, route TEXT,
              tools_called TEXT, answer_status TEXT, evidence_count INTEGER DEFAULT 0,
              evidence_refs TEXT, claim_bindings TEXT, latency_ms INTEGER,
              refused INTEGER DEFAULT 0, error TEXT, trace_json TEXT,
              feedback TEXT, feedback_note TEXT, feedback_by TEXT, feedback_at TEXT,
              created_at TEXT DEFAULT (datetime('now')))"""
        )
        con.execute(
            "INSERT INTO agent_qa_log(channel, question, answer_status, evidence_count) "
            "VALUES(?,?,?,?)",
            ("feishu_dm", "历史真实题", "grounded", 2),
        )
        con.commit()

        qa_log.ensure_schema(con)
        con.commit()

        cols = {r["name"] for r in con.execute("PRAGMA table_info(agent_qa_log)")}
        assert "source" in cols
        # 历史行必须被回填，否则默认 source=feishu 过滤下「一条都没有」
        assert qa_log.list_turns(con, source="feishu")["total"] == 1
        assert qa_log.get_turn(con, 1)["source"] == "feishu"
        # 且事务未被毒化：还能继续查询
        assert qa_log.stats(con, days=3650, source="feishu")["total"] == 1
    finally:
        try:
            con.execute("DROP TABLE IF EXISTS agent_qa_log")
            con.commit()
        except Exception:
            pass
        con.close()


def test_ensure_schema_is_idempotent():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        con.commit()
        qa_log.ensure_schema(con)
        con.commit()
        cols = {r["name"] for r in con.execute("PRAGMA table_info(agent_qa_log)")}
        assert "source" in cols
    finally:
        con.close()


def test_stats_uses_no_sqlite_only_datetime_sql():
    """stats() 不得依赖 SQLite 专有 `datetime('now', ?)`。

    adapt_sql 只重写无参的 `datetime('now')`；带参形式在 PG 上会直接报函数不存在，
    且被 stats 的 except 吞掉 → 概览恒为 0。这里用假连接断言下发的 SQL 不含该写法。
    """
    seen: list[str] = []

    class _Con:
        dialect = "postgresql"

        def execute(self, sql, params=()):
            seen.append(" ".join(str(sql).split()))

            class R:
                def fetchone(self):
                    return {"c": 0}

                def __iter__(self):
                    return iter([])

            return R()

    qa_log.stats(_Con(), days=7, source="feishu")
    assert seen, "stats 应至少下发一条查询"
    for sql in seen:
        assert "datetime(" not in sql.lower(), f"stats SQL 含 SQLite 专有函数: {sql}"


def test_stats_windows_by_cutoff_and_counts_rows():
    con = db.connect()
    try:
        qa_log.ensure_schema(con)
        _reset(con)
        qa_log.record_answer(con, envelope={"channel": "feishu_dm"}, answer=_answer(), question="近题")
        con.commit()
        s = qa_log.stats(con, days=7, source="feishu")
        assert s["total"] == 1
        assert s["by_status"].get("grounded") == 1
        # 0 天窗口：cutoff 是「现在」，历史行不计入（证明不是恒 0 也不是恒全量）
        assert qa_log.stats(con, days=0, source="feishu")["total"] == 0
    finally:
        con.close()



class _AbortedTxError(Exception):
    """模拟 psycopg2 的 InFailedSqlTransaction。"""


class _FakePgCon:
    """模拟 Postgres：任一语句报错后，事务进入 aborted，后续语句全部报错。

    只有 ROLLBACK TO SAVEPOINT 才能解除 aborted。SQLite 不会这样，
    所以线上这个 bug 在本地 SQLite 测试里测不出来——必须显式模拟。
    """

    dialect = "postgresql"

    def __init__(self, cols):
        self.cols = set(cols)
        self.aborted = False
        self.savepoints: list[str] = []
        self.executed: list[str] = []

    def execute(self, sql, params=()):
        s = " ".join(str(sql).split())
        self.executed.append(s)
        up = s.upper()
        if up.startswith("SAVEPOINT"):
            self.savepoints.append(s.split()[-1])
            return self
        if up.startswith("RELEASE SAVEPOINT"):
            if self.savepoints:
                self.savepoints.pop()
            return self
        if up.startswith("ROLLBACK TO SAVEPOINT"):
            self.aborted = False
            return self
        if self.aborted:
            raise _AbortedTxError("current transaction is aborted")

        # information_schema 查询：返回列是否存在
        if "INFORMATION_SCHEMA.COLUMNS" in up:
            name = (params or ("", ""))[-1]
            self._rows = [{"1": 1}] if name in self.cols else []
            return self
        if up.startswith("ALTER TABLE") and "ADD COLUMN" in up:
            rest = s.split("ADD COLUMN", 1)[1].strip()
            rest = re.sub(r"^IF\s+NOT\s+EXISTS\s+", "", rest, flags=re.IGNORECASE)
            col = rest.split()[0]
            if col not in self.cols:
                self.cols.add(col)
            return self
        if up.startswith("CREATE INDEX") and " ON AGENT_QA_LOG(" in up:
            # 索引列必须在表里，否则 PG 报错
            body = s.split("(", 1)[1]
            for c in body.split(")")[0].split(","):
                if c.strip() and c.strip() not in self.cols:
                    self.aborted = True  # 关键：毒化事务
                    raise _AbortedTxError(f"column {c.strip()} does not exist")
            return self
        if up.startswith("UPDATE AGENT_QA_LOG SET SOURCE"):
            return self
        return self

    def fetchone(self):
        return (getattr(self, "_rows", None) or [None])[0]

    def fetchall(self):
        return getattr(self, "_rows", [])

    def commit(self):
        self.aborted = False

    def rollback(self):
        self.aborted = False

    def close(self):
        pass


def test_pg_migration_adds_source_before_index_and_survives_abort():
    """PG 回归：老表缺 source 时，补列必须先于建索引，否则事务被毒化、列永远补不上。"""
    con = _FakePgCon(cols={"id", "request_id", "channel", "question", "answer_status",
                           "evidence_count", "created_at"})
    qa_log.ensure_schema(con)
    # 补列成功
    assert "source" in con.cols
    # 事务未被毒化：最后没有残留 aborted
    assert con.aborted is False
    # 顺序断言：ADD COLUMN source 出现在 source 索引之前
    add_i = next(
        i for i, s in enumerate(con.executed)
        if s.upper().startswith("ALTER TABLE") and "ADD COLUMN" in s.upper() and "SOURCE" in s.upper()
    )
    idx_i = next(
        i for i, s in enumerate(con.executed)
        if s.upper().startswith("CREATE INDEX") and "IDX_QA_LOG_SOURCE" in s.upper()
    )
    assert add_i < idx_i, "source 补列必须早于 source 索引，否则 PG 事务会被 abort"
