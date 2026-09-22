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



def ensure_schema(con) -> None:
    """SQLite / Postgres 均可反复调用。"""
    pg = getattr(con, "dialect", "sqlite") == "postgresql"
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    ddls = [
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
        "CREATE INDEX IF NOT EXISTS idx_qa_log_created ON agent_qa_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_channel ON agent_qa_log(channel, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_source ON agent_qa_log(source, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_status ON agent_qa_log(answer_status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_session ON agent_qa_log(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_qa_log_open_id ON agent_qa_log(feishu_open_id)",
    ]
    for ddl in ddls:
        try:
            con.execute(ddl)
        except Exception as e:
            _log.warning("qa_log ddl failed: %s", e)
    # 旧库补列：source
    try:
        cols = {r["name"] if hasattr(r, "keys") else r[1] for r in con.execute("PRAGMA table_info(agent_qa_log)")}
        if "source" not in cols and not pg:
            con.execute("ALTER TABLE agent_qa_log ADD COLUMN source TEXT")
    except Exception:
        pass
    if pg:
        try:
            con.execute("ALTER TABLE agent_qa_log ADD COLUMN IF NOT EXISTS source TEXT")
        except Exception:
            pass


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
