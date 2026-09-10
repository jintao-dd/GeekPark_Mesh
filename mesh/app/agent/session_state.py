"""Session Context — Colleague Agent v1。

只服务：指代、话题连续性、mode 切换。
不得作为企业事实来源；Follow-up 必须重新 Retrieval + Evidence。
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TopicFrame:
    """可压栈的企业话题快照。"""

    entities: list[str] = field(default_factory=list)
    topic: str = ""
    issue: str = ""
    period: str = ""
    team: str = ""
    frame: str = ""  # contact|relations|progress|about
    query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TopicFrame":
        if not d:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SessionContextState:
    session_key: str = ""
    turn_id: int = 0
    active_entities: list[str] = field(default_factory=list)
    active_team: str = ""
    active_issue: str = ""
    active_period: str = ""
    active_topic: str = ""
    last_intent: str = ""
    last_route: str = ""
    last_query: str = ""
    last_query_refs: list[str] = field(default_factory=list)
    last_evidence_refs: list[str] = field(default_factory=list)
    last_topic_frame: str = ""
    unresolved_references: list[str] = field(default_factory=list)
    conversation_mode: str = ""  # enterprise|chat|clarify|meta|error
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    topic_stack: list[dict[str, Any]] = field(default_factory=list)
    # Stage 2A Colleague Core（仅 session；非长期 Memory）
    colleague_relationship: str = "internal-colleague"
    colleague_pref: str = ""
    colleague_user_tone: str = "neutral"
    colleague_emotional_tone: str = "neutral"
    colleague_feedback: str = ""
    colleague_goal: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "SessionContextState":
        if not d:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        raw = {k: v for k, v in d.items() if k in known}
        return cls(**raw)

    def current_topic(self) -> TopicFrame:
        return TopicFrame(
            entities=list(self.active_entities or []),
            topic=self.active_topic or "",
            issue=self.active_issue or "",
            period=self.active_period or "",
            team=self.active_team or "",
            frame=self.last_topic_frame or "",
            query=self.last_query or "",
        )

    def push_topic(self) -> None:
        snap = self.current_topic().to_dict()
        if not (snap.get("entities") or snap.get("topic") or snap.get("query")):
            return
        stack = list(self.topic_stack or [])
        stack.append(snap)
        self.topic_stack = stack[-8:]

    def restore_topic(self) -> bool:
        stack = list(self.topic_stack or [])
        if not stack:
            return False
        snap = TopicFrame.from_dict(stack.pop())
        self.topic_stack = stack
        if snap.entities:
            self.active_entities = list(snap.entities)
        if snap.topic:
            self.active_topic = snap.topic
        if snap.issue:
            self.active_issue = snap.issue
            self.active_period = snap.period or snap.issue
        if snap.team:
            self.active_team = snap.team
        if snap.frame:
            self.last_topic_frame = snap.frame
        if snap.query:
            self.last_query = snap.query
        self.conversation_mode = "enterprise"
        return True

    def append_turn(self, *, role: str, text: str, route: str = "") -> None:
        turns = list(self.recent_turns or [])
        # Stage 2A：助手回复可能更长，历史保留放宽（仍截断防爆）
        cap = 900 if role == "assistant" else 500
        turns.append(
            {
                "role": role,
                "text": (text or "")[:cap],
                "route": route,
                "turn_id": self.turn_id,
            }
        )
        self.recent_turns = turns[-12:]


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
