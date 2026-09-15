"""Thread-local progress callback for thinking-card updates during Supervisor work."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

_ProgressCb = Callable[[str], None]
_progress_cb: ContextVar[_ProgressCb | None] = ContextVar("mesh_supervisor_progress", default=None)


def set_progress_callback(cb: _ProgressCb | None):
    return _progress_cb.set(cb)


def reset_progress_callback(token) -> None:
    try:
        _progress_cb.reset(token)
    except Exception:
        pass


def emit_progress(label: str) -> None:
    cb = _progress_cb.get()
    if not cb or not label:
        return
    try:
        cb(str(label))
    except Exception:
        pass
