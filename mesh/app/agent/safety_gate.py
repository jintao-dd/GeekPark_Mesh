"""Wave 1 SafetyGate — 规则门，不是 Agent。

仅拦：草稿/原文、能力越权。其余交给 Colleague。
"""
from __future__ import annotations

import re
from typing import Literal

from . import conversation as conv

RefuseKind = Literal["draft", "capability", ""]


def check_refuse(text: str) -> tuple[RefuseKind, str]:
    """返回 (kind, notes)。kind 空表示不拦。"""
    q = conv.normalize_query(text)
    if not q:
        return "", ""
    if conv._DRAFT_RAW.search(q) and not conv._META.search(q):
        if re.search(r"(看|读|查|打开|给我|导出).*(草稿|draft|原文|raw|未上线)", q, re.I) or re.search(
            r"(草稿|draft|原文|raw|未上线).*(内容|全文|json)", q, re.I
        ) or re.search(r"直接查库|查\s*sources", q, re.I):
            return "draft", "draft_raw"
    if conv._CAPABILITY_REFUSE.search(q):
        return "capability", "capability_boundary"
    return "", ""
