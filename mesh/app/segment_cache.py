"""T13 段级抽取缓存：按 segment 内容 hash 复用未变段落的条目。

规则
----
- digest = sha256(stype|title|text)[:20]
- unchanged → reuse 上轮该段条目（不调 LLM）
- changed / new → extract_items
- deleted → 不进入结果（随 DELETE+INSERT 自然消失）

条目通过 pointer 前缀 ``§sd:{digest}|`` 持久化 digest；provenance 比对会剥掉该前缀。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SEG_PREFIX_RE = re.compile(r"^§sd:([a-f0-9]{16,64})\|")


def segment_digest(*, stype: str, title: str, text: str) -> str:
    blob = f"{stype or ''}|{title or ''}|{text or ''}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def stamp_segment_digest(item: dict, digest: str) -> dict:
    """写入 _segment_digest，并编码进 pointer 以便落库后仍可恢复。"""
    it = dict(item) if isinstance(item, dict) else {}
    dig = (digest or "").strip()
    if not dig:
        return it
    it["_segment_digest"] = dig
    ptr = it.get("pointer") or ""
    # 避免重复叠前缀
    _old, bare = parse_segment_digest(ptr)
    it["pointer"] = f"§sd:{dig}|{bare}"
    return it


def parse_segment_digest(pointer: str | None) -> tuple[str | None, str]:
    p = pointer or ""
    m = SEG_PREFIX_RE.match(p)
    if not m:
        return None, p
    return m.group(1), p[m.end() :]


def strip_segment_digest_prefix(pointer: str | None) -> str:
    _d, bare = parse_segment_digest(pointer)
    return bare


def digest_from_item(it: dict) -> str | None:
    if not isinstance(it, dict):
        return None
    d = (it.get("_segment_digest") or "").strip()
    if d:
        return d
    d2, _ = parse_segment_digest(it.get("pointer"))
    return d2


def group_items_by_segment_digest(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        d = digest_from_item(it)
        if not d:
            continue
        out.setdefault(d, []).append(it)
    return out


def clone_item_for_reuse(it: dict, *, digest: str) -> dict:
    """复用时去掉 DB id，保留内容字段并重盖 digest。"""
    skip = {"id", "merged_into"}
    raw = {k: v for k, v in it.items() if k not in skip}
    # entities/roles/signals 可能是 JSON 字符串（来自 DB）
    for k in ("entities", "roles", "signals", "source_labels"):
        v = raw.get(k)
        if isinstance(v, str):
            try:
                raw[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
    return stamp_segment_digest(raw, digest)


def build_segment_digests_meta(segments: list[Any]) -> list[str]:
    digests: list[str] = []
    for seg in segments or []:
        digests.append(
            segment_digest(
                stype=getattr(seg, "stype", "") or "",
                title=getattr(seg, "title", "") or "",
                text=getattr(seg, "text", "") or "",
            )
        )
    return digests
