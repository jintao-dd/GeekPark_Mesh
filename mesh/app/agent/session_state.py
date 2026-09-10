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
    # Feishu Hands：待确认写操作（非企业事实）
    pending_write: dict[str, Any] | None = None
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
        # Stage 2A / v3：助手回复可较长，历史保留放宽（仍截断防爆）
        cap = 2000 if role == "assistant" else 800
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
    # 飞书群/私聊：一会话一键。不要用 reply root / thread 切分，否则确认写必丢 pending。
    if ch in ("feishu_group", "feishu_dm"):
        return base
    if thread_id:
        base += f":t{thread_id}"
    elif session_id:
        base += f":s{session_id}"
    return base


def pending_key_of(
    *,
    channel: str = "",
    chat_id: str = "",
    feishu_open_id: str = "",
) -> str:
    """待确认写操作的稳定键（比 session_key 更粗，跨 reply 存活）。"""
    ch = (channel or "").strip()
    if ch == "feishu_group" and chat_id:
        return f"pend:grp:{chat_id}"
    if chat_id and ch.startswith("feishu"):
        return f"pend:chat:{chat_id}"
    if feishu_open_id:
        return f"pend:dm:{feishu_open_id}"
    if chat_id:
        return f"pend:chat:{chat_id}"
    return ""


_PENDING_LOCK = threading.Lock()
_PENDING_STORE: dict[str, dict[str, Any]] = {}
_PENDING_TTL_SEC = 2 * 3600


def _data_dir() -> "Path":
    import os
    from pathlib import Path

    raw = (os.environ.get("MESH_AGENT_SESSION_DIR") or "").strip()
    if raw:
        p = Path(raw)
    else:
        # /srv/mesh/data in container；本地则相对 cwd
        p = Path("data") / "agent_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(key: str) -> str:
    import hashlib

    h = hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:40]
    return h


def _disk_path(kind: str, key: str):
    return _data_dir() / f"{kind}_{_safe_name(key)}.json"


def _disk_write(kind: str, key: str, payload: dict[str, Any] | None) -> None:
    import json

    path = _disk_path(kind, key)
    if not payload:
        try:
            path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        return
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _disk_read(kind: str, key: str) -> dict[str, Any] | None:
    import json

    path = _disk_path(kind, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_pending_write(key: str, pending: dict[str, Any] | None) -> None:
    """跨 uvicorn worker 共享：内存 + 磁盘（workers>1 时内存不可靠）。"""
    if not key:
        return
    now = time.time()
    with _PENDING_LOCK:
        if not pending or not pending.get("tool"):
            _PENDING_STORE.pop(key, None)
            _disk_write("pending", key, None)
            return
        blob = dict(pending)
        blob["_saved_at"] = now
        _PENDING_STORE[key] = blob
        _disk_write("pending", key, blob)


def load_pending_write(key: str) -> dict[str, Any] | None:
    if not key:
        return None
    now = time.time()
    with _PENDING_LOCK:
        st = _PENDING_STORE.get(key)
        if not st:
            st = _disk_read("pending", key)
            if st:
                _PENDING_STORE[key] = st
        if not st:
            return None
        if now - float(st.get("_saved_at") or 0) > _PENDING_TTL_SEC:
            _PENDING_STORE.pop(key, None)
            _disk_write("pending", key, None)
            return None
        out = {k: v for k, v in st.items() if k != "_saved_at"}
        return out if out.get("tool") else None


def clear_pending_write(key: str) -> None:
    if not key:
        return
    with _PENDING_LOCK:
        _PENDING_STORE.pop(key, None)
        _disk_write("pending", key, None)


def load(session_key: str) -> SessionContextState:
    if not session_key:
        return SessionContextState()
    now = time.time()
    with _LOCK:
        st = _STORE.get(session_key)
        if not st:
            raw = _disk_read("session", session_key)
            if raw:
                st = SessionContextState.from_dict(raw)
                _STORE[session_key] = st
        if not st:
            return SessionContextState(session_key=session_key)
        if st.updated_at and now - st.updated_at > _TTL_SEC:
            _STORE.pop(session_key, None)
            _disk_write("session", session_key, None)
            return SessionContextState(session_key=session_key)
        return SessionContextState.from_dict(st.to_dict())


def save(state: SessionContextState) -> None:
    if not state.session_key:
        return
    state.updated_at = time.time()
    blob = state.to_dict()
    with _LOCK:
        _STORE[state.session_key] = SessionContextState.from_dict(blob)
        _disk_write("session", state.session_key, blob)


def clear(session_key: str = "") -> None:
    with _LOCK:
        if session_key:
            _STORE.pop(session_key, None)
            _disk_write("session", session_key, None)
        else:
            _STORE.clear()
    with _PENDING_LOCK:
        if not session_key:
            _PENDING_STORE.clear()
            # 测试清空：扫盘
            try:
                for p in _data_dir().glob("*.json"):
                    p.unlink()
            except Exception:
                pass


def reset_for_tests() -> None:
    clear()
    with _PENDING_LOCK:
        _PENDING_STORE.clear()
        try:
            for p in _data_dir().glob("*.json"):
                p.unlink()
        except Exception:
            pass
