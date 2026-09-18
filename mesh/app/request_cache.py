"""请求级缓存：绑定单个请求生命周期，避免跨请求污染。

使用方式
--------
在请求入口创建 RequestCache 实例，随函数栈传递：

    cache = RequestCache()
    company = company_context.assemble(..., request_cache=cache)
    people = person_resolve.resolve_people_in_text(..., request_cache=cache)

请求结束时 cache 随栈释放，无需显式清理。

线程安全
--------
- 同一请求内的多个子线程可共享同一个 RequestCache。
- 读写操作受 _lock 保护，避免并发竞争。
- 不依赖 threading.local，因此子线程访问不需要从主线程继承 threadlocal。
"""
from __future__ import annotations

import threading
import time
from typing import Any


class RequestCache:
    """单个请求内的 TTL 缓存。"""

    def __init__(self, ttl_s: float = 60.0):
        self._ttl_s = ttl_s
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _gc(self) -> None:
        now = time.monotonic()
        stale = [k for k, (ts, _v) in self._data.items() if now - ts > self._ttl_s]
        for k in stale:
            self._data.pop(k, None)

    def get(self, key: str) -> Any | None:
        with self._lock:
            ent = self._data.get(key)
            if ent is None:
                return None
            ts, value = ent
            if time.monotonic() - ts > self._ttl_s:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._gc()
