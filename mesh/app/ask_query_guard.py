"""Ask 入口 Query 校验：拦截明显无意义输入，避免进入 FTS/Hybrid 噪声检索。"""
from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MULTI_WORD_EN = re.compile(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})+")

_GUARD_ANSWER = (
    "无法识别该问题，请用中文或常见英文公司/产品名重新描述。"
    "未在已上线周报中检索。"
)


def is_nonsense_query(q: str) -> bool:
    """True = 明显乱码/无意义，不应触发全文检索。"""
    q = (q or "").strip()
    if not q or len(q) < 10:
        return False
    if _CJK_RE.search(q):
        return False
    if _MULTI_WORD_EN.search(q):
        return False
    compact = re.sub(r"\s+", "", q)
    if len(compact) < 10:
        return False
    if re.fullmatch(r"[A-Za-z0-9]+", compact):
        return True
    alnum = sum(c.isalnum() for c in compact)
    if len(compact) >= 14 and alnum / len(compact) > 0.92:
        return True
    return False


def guard_direct_answer(q: str) -> str | None:
    if is_nonsense_query(q):
        return _GUARD_ANSWER
    return None
