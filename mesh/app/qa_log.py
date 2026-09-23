"""飞书 / Agent 回答质量采集：每轮问答落库，可回看、可筛选、可人工标注。

为什么需要：
- 线上飞书问答此前只写 `ask_log`（query/mode/n_hits/latency），**没有答案文本、证据、answer_status**，
  出问题无法回看，也就无法形成可复现的失败样本。
- 质量解冻的前提是「可复现的答错/无证据乱答」；本模块负责把这条证据链先建起来。

两层都记：
- 事实层：answer_status / evidence_count / evidence_refs / claim_bindings / tools
- 对话层：question / answer_text / route / intent / latency（供人工判断「像不像同事」）

**来源隔离（防自污染）：** 真实飞书流量默认落库；评测 / HTTP harness 默认**不落**，
否则在 tmesh/prod 容器里跑评测会把测试问句当成真实样本写进质量表。
开关：`MESH_QA_LOG=0` 全局关；`MESH_QA_LOG_EVAL=1` 允许评测/HTTP 也落库（打 `source=eval`）。

不改大脑判定，只做记录。写入失败绝不影响回复。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

_log = logging.getLogger("mesh.qa_log")

# 人工反馈取值
FEEDBACK_OK = "good"
FEEDBACK_BAD = "bad"

# 采集来源（落库时打标，回看可筛）
SOURCE_FEISHU = "feishu"  # 真实飞书私聊 / 群聊
SOURCE_EVAL = "eval"  # 评测脚本 / 压测 / 本地 harness
SOURCE_HTTP = "http"  # 外部 HTTP 调用

# 真实飞书渠道：只有这些默认落库
_FEISHU_CHANNELS = ("feishu_dm", "feishu_group")


def _truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _collect_enabled() -> bool:
    """全局开关。默认开；显式设 0/false/no/off 才关。"""
    return str(os.environ.get("MESH_QA_LOG") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _eval_collect_enabled() -> bool:
    """评测/HTTP 是否也落库。默认关，避免污染真实样本。"""
    return _truthy(os.environ.get("MESH_QA_LOG_EVAL"))


def should_record(channel: str) -> bool:
    """判定该渠道是否应落库。真实飞书默认落；其余需显式开。"""
    if not _collect_enabled():
        return False
    ch = (channel or "").strip()
    if ch in _FEISHU_CHANNELS:
        return True
    return _eval_collect_enabled()


def source_of(channel: str) -> str:
    ch = (channel or "").strip()
    if ch in _FEISHU_CHANNELS:
        return SOURCE_FEISHU
    if ch == "harness":
        return SOURCE_EVAL
    if ch == "web":
        return SOURCE_HTTP
    return SOURCE_HTTP



def _safe_ddl(con, sql: str, *, pg: bool) -> None:
    """执行 DDL；单条失败不得毒化外层事务。

    Postgres 里任意语句报错会把整个事务置为 aborted，之后所有语句都返回
    InFailedSqlTransaction。这里用 SAVEPOINT 把失败限制在本语句内，
    否则一条索引建不起来就会连累后面的补列，且被 except 吞掉后无从察觉。
    """
    if pg:
        try:
            con.execute("SAVEPOINT qa_log_ddl")
        except Exception:
            pass
    try:
        con.execute(sql)
        if pg:
            con.execute("RELEASE SAVEPOINT qa_log_ddl")
    except Exception as e:
        _log.warning("qa_log ddl failed: %s | sql=%s", e, sql[:90])
        if pg:
            try:
                con.execute("ROLLBACK TO SAVEPOINT qa_log_ddl")
            except Exception:
                pass


def _add_column_if_missing(con, table: str, col: str, decl: str, *, pg: bool) -> bool:
    """补列；返回是否本次新建（用于决定要不要回填历史数据）。"""
    if pg:
        try:
            exists = bool(
                con.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
                    (table, col),
                ).fetchone()
            )
        except Exception:
            exists = False
        if exists:
            return False
        _safe_ddl(con, f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {decl}", pg=pg)
        return True
    try:
        cols = {
            (r["name"] if hasattr(r, "keys") else r[1])
            for r in con.execute(f"PRAGMA table_info({table})")
        }
    except Exception:
        return False
    if col in cols:
        return False
    _safe_ddl(con, f"ALTER TABLE {table} ADD COLUMN {col} {decl}", pg=pg)
    return True


def _backfill_source(con, *, pg: bool) -> None:
    """老库补出 source 后，按 channel 回填历史行。

    否则这些真实飞书样本 source 为 NULL，会被回看台默认的 source=feishu 过滤掉，
    看起来「一条记录都没有」。
    """
    cases = " ".join(
        f"WHEN channel = ? THEN ?" for _ in _FEISHU_CHANNELS
    )
    params: list[Any] = []
    for ch in _FEISHU_CHANNELS:
        params.extend([ch, SOURCE_FEISHU])
    params.extend(["harness", SOURCE_EVAL])
    sql = (
        f"UPDATE agent_qa_log SET source = CASE {cases} "
        f"WHEN channel = ? THEN ? ELSE ? END WHERE source IS NULL"
    )
    params.append(SOURCE_HTTP)
    try:
        con.execute(sql, params)
    except Exception as e:
        _log.warning("qa_log source backfill failed: %s", e)


def ensure_schema(con) -> None:
    """SQLite / Postgres 均可反复调用。"""
    pg = getattr(con, "dialect", "sqlite") == "postgresql"
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    _safe_ddl(
        con,
        f"""CREATE TABLE IF NOT EXISTS agent_qa_log(
          id {pk},
          request_id TEXT,
          source TEXT,
          channel TEXT,
          feishu_open_id TEXT,
          mesh_user_id INTEGER,
          session_id TEXT,
          chat_id TEXT,
          question TEXT NOT NULL,
          answer_text TEXT,
          intent TEXT,
          route TEXT,
          tools_called TEXT,
          answer_status TEXT,
          evidence_count INTEGER DEFAULT 0,
          evidence_refs TEXT,
          claim_bindings TEXT,
          latency_ms INTEGER,
          refused INTEGER DEFAULT 0,
          error TEXT,
          trace_json TEXT,
          feedback TEXT,
          feedback_note TEXT,
          feedback_by TEXT,
          feedback_at TEXT,
          created_at TEXT DEFAULT (datetime('now'))
        )""",
        pg=pg,
    )
    # 旧库补列必须在建索引之前：先建 source 索引会因缺列报错，
    # 进而 abort 整个 PG 事务，把后面的补列一起带没。
    added = _add_column_if_missing(con, "agent_qa_log", "source", "TEXT", pg=pg)
    if added:
        _backfill_source(con, pg=pg)
    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_qa_log_created ON agent_qa_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_channel ON agent_qa_log(channel, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_source ON agent_qa_log(source, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_status ON agent_qa_log(answer_status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_session ON agent_qa_log(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_open_id ON agent_qa_log(feishu_open_id)",
    ):
        _safe_ddl(con, idx, pg=pg)


def _json_dump(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


def _slim_trace(trace: Any) -> str:
    """只留质量排查用得上的 trace 字段，避免把整包 payload 写进库。"""
    if not isinstance(trace, dict):
        return "{}"
    keep = (
        "conversation_route",
        "colleague_action",
        "colleague_v3",
        "supervisor",
        "crm_metric",
        "cross_op",
        "source_tier",
        "n_hits",
        "pending_write",
        "router_llm_used",
        "llm_used",
        "model_used",
        "ask_query",
        "refuse_reason",
        "deny_reason",
    )
    out = {k: trace.get(k) for k in keep if k in trace}
    timings = trace.get("timings")
    if isinstance(timings, dict):
        out["timings"] = timings
    return _json_dump(out)


def _answer_status(answer: dict[str, Any]) -> str:
    """复用 observability 的判定口径，保证与 HTTP 出口一致。"""
    try:
        from .agent.observability import _answer_status as _st

        return _st(answer)
    except Exception:
        if answer.get("refused"):
            return "refused"
        return "unknown"


def record_answer(
    con,
    *,
    envelope: dict[str, Any] | None,
    answer: dict[str, Any],
    question: str,
    latency_ms: float | int | None = None,
    request_id: str = "",
    error: str = "",
) -> int | None:
    """记录一轮问答。任何异常都吞掉并记日志，绝不影响回复。"""
    try:
        env = envelope or {}
        ans = answer or {}
        channel = str(env.get("channel") or "")[:32]
        # 来源隔离：评测/HTTP 默认不落库，避免污染真实样本
        if not should_record(channel):
            return None
        trace = ans.get("trace") if isinstance(ans.get("trace"), dict) else {}
        evidence_refs = list(ans.get("evidence_refs") or [])
        bindings = [
            {
                "claim": (b or {}).get("claim"),
                "status": (b or {}).get("status"),
            }
            for b in (ans.get("claim_bindings") or [])
            if isinstance(b, dict)
        ]
        tools = list(ans.get("tools_called") or [])
        route = str(
            ans.get("conversation_route")
            or trace.get("conversation_route")
            or trace.get("colleague_action")
            or ""
        )
        intent = str(ans.get("intent") or "")
        from .db_conn import insert_id

        return insert_id(
            con,
            """INSERT INTO agent_qa_log(
                 request_id, source, channel, feishu_open_id, mesh_user_id, session_id, chat_id,
                 question, answer_text, intent, route, tools_called,
                 answer_status, evidence_count, evidence_refs, claim_bindings,
                 latency_ms, refused, error, trace_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                (request_id or "")[:64],
                source_of(channel),
                channel,
                str(env.get("feishu_open_id") or env.get("open_id") or "")[:128],
                env.get("mesh_user_id"),
                str(env.get("session_id") or "")[:64],
                str(env.get("chat_id") or "")[:128],
                (question or "")[:4000],
                (str(ans.get("display_text") or ans.get("text") or ""))[:8000],
                intent[:64],
                route[:64],
                _json_dump(tools),
                _answer_status(ans)[:32],
                len(evidence_refs),
                _json_dump(evidence_refs),
                _json_dump(bindings),
                int(latency_ms) if latency_ms is not None else None,
                1 if ans.get("refused") else 0,
                (error or ans.get("deny_reason") or "")[:500],
                _slim_trace(trace),
            ),
        )
    except Exception as e:
        _log.warning("qa_log record failed: %s", e)
        try:
            con.rollback()
        except Exception:
            pass
        return None


def _row_to_turn(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("tools_called", "evidence_refs", "claim_bindings", "trace_json"):
        raw = d.get(k)
        if isinstance(raw, str) and raw:
            try:
                d[k] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[k] = [] if k != "trace_json" else {}
        elif raw is None:
            d[k] = [] if k != "trace_json" else {}
    d["flag"] = flag_of(d)
    return d


def flag_of(turn: dict[str, Any]) -> str:
    """给人工复查排优先级：只标「可能有问题」，不替代人判断。"""
    if turn.get("error"):
        return "error"
    if turn.get("refused"):
        return "refused"
    st = (turn.get("answer_status") or "").lower()
    bindings = turn.get("claim_bindings") or []
    statuses = {
        str((b or {}).get("status") or "").lower()
        for b in bindings
        if isinstance(b, dict)
    }
    if st == "unsupported" or "unsupported" in statuses:
        return "unsupported"
    if st == "weak" or "weak" in statuses:
        return "weak"
    if st in ("unknown", "") and not turn.get("evidence_count"):
        return "no_evidence"
    if st == "unknown":
        return "unknown"
    return ""


def list_turns(
    con,
    *,
    channel: str = "",
    source: str = "",
    answer_status: str = "",
    flag: str = "",
    only_bad: bool = False,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """分页查询。flag 过滤在 Python 侧做（flag 由 answer_status/refused 派生）。"""
    where = ["1=1"]
    params: list[Any] = []
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if source:
        where.append("source = ?")
        params.append(source)
    if answer_status:
        where.append("answer_status = ?")
        params.append(answer_status)
    if q:
        where.append("(question LIKE ? OR answer_text LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if only_bad:
        where.append("feedback = ?")
        params.append(FEEDBACK_BAD)
    sql_where = " AND ".join(where)
    rows = con.execute(
        f"SELECT * FROM agent_qa_log WHERE {sql_where} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, int(limit), int(offset)],
    ).fetchall()
    turns = [_row_to_turn(r) for r in rows]
    if flag:
        turns = [t for t in turns if t.get("flag") == flag]
    total = con.execute(
        f"SELECT COUNT(*) c FROM agent_qa_log WHERE {sql_where}", params
    ).fetchone()["c"]
    return {"turns": turns, "total": int(total or 0), "limit": limit, "offset": offset}


def get_turn(con, turn_id: int) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM agent_qa_log WHERE id=?", (int(turn_id),)).fetchone()
    return _row_to_turn(row) if row else None


def stats(con, *, days: int = 7, source: str = SOURCE_FEISHU) -> dict[str, Any]:
    """近 N 天质量概览：状态分布 / 渠道分布 / 反馈。默认只看真实飞书样本。"""
    window = f"-{int(days)} days"
    by_status: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    by_source: dict[str, int] = {}
    src_clause = " AND source = ?" if source else ""
    src_params: list[Any] = [source] if source else []
    try:
        for r in con.execute(
            "SELECT answer_status, COUNT(*) c FROM agent_qa_log "
            "WHERE created_at >= datetime('now', ?)" + src_clause + " GROUP BY answer_status",
            (window, *src_params),
        ):
            by_status[str(r["answer_status"] or "unknown")] = int(r["c"] or 0)
        for r in con.execute(
            "SELECT channel, COUNT(*) c FROM agent_qa_log "
            "WHERE created_at >= datetime('now', ?)" + src_clause + " GROUP BY channel",
            (window, *src_params),
        ):
            by_channel[str(r["channel"] or "unknown")] = int(r["c"] or 0)
        for r in con.execute(
            "SELECT source, COUNT(*) c FROM agent_qa_log "
            "WHERE created_at >= datetime('now', ?) GROUP BY source",
            (window,),
        ):
            by_source[str(r["source"] or "unknown")] = int(r["c"] or 0)
    except Exception as e:
        _log.warning("qa_log stats failed: %s", e)
    total = sum(by_status.values())
    return {
        "days": days,
        "source": source,
        "total": total,
        "by_status": by_status,
        "by_channel": by_channel,
        "by_source": by_source,
    }


def set_feedback(
    con,
    turn_id: int,
    *,
    feedback: str,
    note: str = "",
    by: str = "",
) -> bool:
    """人工标注 good/bad（空字符串=清除）。"""
    fb = (feedback or "").strip().lower()
    if fb not in ("", FEEDBACK_OK, FEEDBACK_BAD):
        return False
    con.execute(
        "UPDATE agent_qa_log SET feedback=?, feedback_note=?, feedback_by=?, "
        "feedback_at=datetime('now') WHERE id=?",
        (fb or None, (note or "")[:500], (by or "")[:64], int(turn_id)),
    )
    return True
