"""Session Context State — 只帮助理解「用户在说什么」，不作事实来源。

硬约束：
- active_entities / last_* 仅用于指代消解与 query rewrite
- 下一轮必须重新 Retrieval + Evidence，禁止把上一轮答案当事实
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SessionContextState:
    session_key: str = ""
    turn_id: int = 0
    active_entities: list[str] = field(default_factory=list)
    active_team: str = ""
    active_issue: str = ""
    active_period: str = ""
    last_intent: str = ""
    last_route: str = ""
    last_query: str = ""
    last_query_refs: list[str] = field(default_factory=list)
    last_evidence_refs: list[str] = field(default_factory=list)
    last_topic_frame: str = ""  # contact | relations | progress | about
    unresolved_references: list[str] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "SessionContextState":
        if not d:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


_LOCK = threading.Lock()
_STORE: dict[str, SessionContextState] = {}
_TTL_SEC = 6 * 3600


def session_key_of(
    *,
    channel: str,
    feishu_open_id: str = "",
    chat_id: str = "",
    thread_id: str = "",
    session_id: str = "",
    mesh_user_id: int | None = None,
) -> str:
    """稳定会话键：不含 issue（期次变化不应丢对话实体）。"""
    ch = (channel or "web").strip()
    if ch == "feishu_group" and chat_id:
        base = f"grp:{chat_id}"
    elif ch in ("feishu_dm", "harness") and feishu_open_id:
        base = f"dm:{feishu_open_id}"
    elif mesh_user_id:
        base = f"u:{mesh_user_id}"
    else:
        base = f"anon:{session_id or chat_id or 'x'}"
    if thread_id:
        base += f":t{thread_id}"
    elif session_id:
        base += f":s{session_id}"
    return base


def load(session_key: str) -> SessionContextState:
    if not session_key:
        return SessionContextState()
    now = time.time()
    with _LOCK:
        st = _STORE.get(session_key)
        if not st:
            return SessionContextState(session_key=session_key)
        if st.updated_at and now - st.updated_at > _TTL_SEC:
            _STORE.pop(session_key, None)
            return SessionContextState(session_key=session_key)
        return SessionContextState.from_dict(st.to_dict())


def save(state: SessionContextState) -> None:
    if not state.session_key:
        return
    state.updated_at = time.time()
    with _LOCK:
        _STORE[state.session_key] = SessionContextState.from_dict(state.to_dict())


def clear(session_key: str = "") -> None:
    with _LOCK:
        if session_key:
            _STORE.pop(session_key, None)
        else:
            _STORE.clear()


def reset_for_tests() -> None:
    clear()
