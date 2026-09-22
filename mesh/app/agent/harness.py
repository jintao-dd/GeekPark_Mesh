"""Agent v1 本地/HTTP Harness（无飞书事件）。"""
from __future__ import annotations

from typing import Any

from .models import AgentEnvelope
from .observability import attach_observability, new_request_id, timed_run
from .runtime import handle_message


def envelope_from_payload(payload: dict[str, Any]) -> AgentEnvelope:
    p = payload or {}
    return AgentEnvelope(
        text=str(p.get("text") or p.get("q") or p.get("message") or "").strip(),
        channel=str(p.get("channel") or "harness").strip(),
        feishu_open_id=str(p.get("feishu_open_id") or p.get("open_id") or "").strip(),
        mesh_user_id=int(p["mesh_user_id"]) if p.get("mesh_user_id") is not None else None,
        chat_id=str(p.get("chat_id") or "").strip(),
        thread_id=str(p.get("thread_id") or "").strip(),
        session_id=str(p.get("session_id") or "").strip(),
        mapped_teams=list(p.get("mapped_teams") or []),
        contact_sync=str(p.get("contact_sync") or "skipped_no_scope"),
        explicit_team=str(p.get("explicit_team") or p.get("team") or "").strip(),
        explicit_issue=str(p.get("explicit_issue") or p.get("slug") or "").strip(),
        lock_issue=bool(p.get("lock_issue")),
        pinned_issue=str(p.get("pinned_issue") or "").strip(),
        pinned_issue_epoch=int(p.get("pinned_issue_epoch") or 0),
        identity_override=p.get("identity_override"),
        mentions=list(p.get("mentions") or []) if isinstance(p.get("mentions"), list) else [],
    )


def run_harness(con, payload: dict[str, Any]) -> dict[str, Any]:
    p = payload or {}
    request_id = str(p.get("request_id") or "").strip() or new_request_id()
    env = envelope_from_payload(p)
    answer, latency_ms = timed_run(handle_message, con, env)
    d = answer.to_dict()
    # 质量采集：只记真实飞书流量；harness/评测默认不落（防污染），需 MESH_QA_LOG_EVAL=1
    try:
        from .. import qa_log

        if qa_log.should_record(env.channel):
            qa_log.record_answer(
                con,
                envelope=p,
                answer=d,
                question=env.text,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            con.commit()
    except Exception:
        pass
    # 透传检索命中到 observability（若 adapter 写入 trace）
    return attach_observability(
        d,
        request_id=request_id,
        envelope=p,
        latency_ms=latency_ms,
    )