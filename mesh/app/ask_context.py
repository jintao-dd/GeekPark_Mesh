"""Context Router：独立问题 vs 追问（只继承结构化 context_refs）。

规则：
- independent：不继承任何上下文；检索问句不改写
- follow_up：只继承上一 analysis 的 context_refs；新建 analysis_id 后重跑全链路
- 历史 Answer / 自然语言分析结果不得进入证据或成文上下文
- chunk_ids 仅作检索定位种子（soft boost / 问句扩展），不得直接当本轮答案证据
"""
from __future__ import annotations

from typing import Any


_STRONG = (
    "他", "她", "它", "他们", "她们", "那家", "这家", "那家公司", "这家公司",
    "上面", "刚才", "之前", "同上", "继续", "接着", "展开", "详细",
    "第一家", "第二个", "第三家", "还有哪些", "还有呢", "那然后",
)
_WEAK_SHORT = ("还有", "呢", "吗", "咋样", "如何", "怎样", "对比一下")


def empty_refs() -> dict[str, Any]:
    return {
        "analysis_id": "",
        "parent_analysis_id": None,
        "last_user_q": "",
        "entities": [],
        "teams": [],
        "issues": [],
        "chunk_ids": [],
        "item_ids": [],
    }


def _uniq(seq: list, limit: int) -> list:
    out: list = []
    seen: set[str] = set()
    for x in seq or []:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _is_meta_ctx(ctx: dict) -> bool:
    sec = str(ctx.get("章节") or ctx.get("section") or "")
    issue = str(ctx.get("期号") or ctx.get("issue") or "")
    title = str(ctx.get("标题") or ctx.get("title") or "")
    if sec in ("检索范围", "查询说明", "结构化检索"):
        return True
    if issue in ("查询说明", "检索范围"):
        return True
    if "事实表算出" in title:
        return True
    return False


def build_context_refs(
    *,
    analysis_id: str,
    q: str = "",
    contexts: list[dict] | None = None,
    sources: list[dict] | None = None,
    cross: dict | None = None,
    parent_analysis_id: str | None = None,
) -> dict[str, Any]:
    """从本轮分析结果物化结构化 refs（不含 Answer 正文）。"""
    entities: list[str] = []
    teams: list[str] = []
    issues: list[str] = []
    chunk_ids: list[str] = []
    item_ids: list[str] = []

    for ctx in contexts or []:
        if not isinstance(ctx, dict) or _is_meta_ctx(ctx):
            continue
        team = (ctx.get("归属团队") or ctx.get("团队") or "").strip()
        if team:
            teams.append(team)
        issue = (ctx.get("期号") or ctx.get("issue") or "").strip()
        if issue:
            issues.append(issue)
        title = (ctx.get("标题") or ctx.get("title") or "").strip()
        if title and len(title) <= 40:
            entities.append(title)
        cid = ctx.get("chunk_id") or ctx.get("chunkId")
        if cid:
            chunk_ids.append(str(cid))
        iid = ctx.get("条目ID") or ctx.get("item_id")
        if iid is not None and str(iid).strip():
            item_ids.append(str(iid))

    for s in sources or []:
        if not isinstance(s, dict):
            continue
        if s.get("source"):
            teams.append(str(s["source"]))
        if s.get("issue"):
            issues.append(str(s["issue"]))

    if isinstance(cross, dict):
        for e in cross.get("same_entities") or []:
            if isinstance(e, str):
                entities.append(e)
            elif isinstance(e, dict) and e.get("name"):
                entities.append(str(e["name"]))

    return {
        "analysis_id": analysis_id or "",
        "parent_analysis_id": parent_analysis_id,
        "last_user_q": (q or "").strip()[:500],
        "entities": _uniq(entities, 30),
        "teams": _uniq(teams, 12),
        "issues": _uniq(issues, 12),
        "chunk_ids": _uniq(chunk_ids, 24),
        "item_ids": _uniq(item_ids, 24),
    }


def _last_user_q(messages: list[dict] | None) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


def extract_refs_from_messages(messages: list[dict] | None) -> dict[str, Any]:
    """从会话消息 meta 取上一轮 assistant 的 context_refs（忽略 Answer 正文）。"""
    for m in reversed(messages or []):
        if m.get("role") != "assistant":
            continue
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        refs = meta.get("context_refs")
        if isinstance(refs, dict) and (
            refs.get("entities")
            or refs.get("chunk_ids")
            or refs.get("last_user_q")
            or refs.get("teams")
        ):
            return dict(refs)
    # 无结构化 refs 时，仅用上一用户问句作检索补全种子（仍不含 Answer）
    lu = _last_user_q(messages)
    if not lu:
        return empty_refs()
    refs = empty_refs()
    refs["last_user_q"] = lu[:500]
    return refs


def expand_search_q(q: str, refs: dict[str, Any] | None) -> str:
    """用 refs 补全检索问句；不把历史 Answer 拼进去。"""
    q = (q or "").strip()
    refs = refs or {}
    parts: list[str] = []
    lu = (refs.get("last_user_q") or "").strip()
    if lu and lu != q and lu not in q:
        parts.append(lu)
    parts.append(q)
    for e in (refs.get("entities") or [])[:5]:
        e = str(e).strip()
        if e and e not in q and e not in lu:
            parts.append(e)
    for t in (refs.get("teams") or [])[:2]:
        t = str(t).strip()
        if t and t not in q:
            parts.append(t)
    out = " ".join(parts).strip()
    return out[:500] or q


def is_followup(q: str, *, has_prior: bool) -> tuple[bool, str]:
    q = (q or "").strip()
    if not q or not has_prior:
        return False, "no_history" if not has_prior else "empty"
    if any(c in q for c in _STRONG) or q.endswith(("呢", "呢？", "呢?")):
        return True, "anaphora"
    # 短句不自动判追问，避免「面壁智能」等独立专名被误路由
    if len(q) < 20 and any(c in q for c in _WEAK_SHORT):
        return True, "weak_short"
    return False, "standalone"


def route(
    q: str,
    messages: list[dict] | None = None,
    *,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """
    返回:
      kind: independent | followup
      history: 恒为空（禁止把对话/Answer 传给成文）
      context_refs: followup 时为上一 analysis 的结构化引用；independent 为空
      search_q: 检索问句
      parent_analysis_id: 若有
      reason: 路由原因
    """
    # 兼容旧调用：history 实为 messages
    msgs = list(messages if messages is not None else (history or []))
    q = (q or "").strip()
    if not q:
        return {
            "kind": "independent",
            "history": [],
            "context_refs": empty_refs(),
            "search_q": "",
            "parent_analysis_id": None,
            "reason": "empty",
        }

    prior_refs = extract_refs_from_messages(msgs) if msgs else empty_refs()
    has_prior = bool(msgs) or bool(prior_refs.get("last_user_q"))
    follow, reason = is_followup(q, has_prior=has_prior)

    if not follow:
        return {
            "kind": "independent",
            "history": [],
            "context_refs": empty_refs(),
            "search_q": q,
            "parent_analysis_id": None,
            "reason": reason if has_prior else "no_history",
        }

    # 追问：只继承结构化 refs；search_q 用 refs 补全；history 恒空
    inherited = dict(prior_refs)
    parent_id = inherited.get("analysis_id") or None
    return {
        "kind": "followup",
        "history": [],
        "context_refs": inherited,
        "search_q": expand_search_q(q, inherited),
        "parent_analysis_id": parent_id,
        "reason": reason,
    }


def needs_cross(q: str, source_reports: list[dict] | None, *, mode: str = "") -> bool:
    reports = [r for r in (source_reports or []) if isinstance(r, dict)]
    if len(reports) <= 1:
        return False
    if (mode or "").startswith("structured"):
        return True
    cues = (
        "同时", "交叉", "对比", "交集", "差集", "共同", "两边", "两队", "两方",
        "冲突", "印证", "也都", "也是", "分别", "各自", "多源", "都在",
        "哪些也", "谁也在", "重叠", "重合",
    )
    qq = q or ""
    if any(c in qq for c in cues):
        return True
    return False
