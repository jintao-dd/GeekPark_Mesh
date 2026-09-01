"""Narrative 展示层规范化：只改 source_label / detail，绝不反推 owner_team。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .aggregator import INVALID_OWNER_TEAMS, sanitize_owner_team
from . import ingest

# 归属式后缀：团队名出现在开头 + 此后缀 → 视为「来源归属宣称」，非内容实体
_ATTRIBUTION_SUFFIX = re.compile(
    r"^(?:"
    r"(?:团队)?(?:建联|沟通|见人|访谈|采访|例会|会议|周报|工作进展|数据|妙记|转写)?记录"
    r"|(?:团队)?建联"
    r"|数据"
    r"|妙记(?:转写)?"
    r"|(?:部门|团队)?周例会"
    r")"
    r"(?:\s*[·•：:]\s*|\s+|$)",
)

# detail 行：「X记录：」
_DETAIL_ATTRIBUTION = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9 /·&]{2,24})\s*记录\s*[：:]\s*(.*)$",
    re.S,
)


@dataclass(frozen=True)
class NarrativeCleanResult:
    original: str
    cleaned: str
    action: str  # keep | strip_prefix | neutralize | needs_review
    reason: str
    needs_review: bool = False


def _formal_teams() -> list[str]:
    return sorted(
        (t for t in ingest.TEAMS if t not in INVALID_OWNER_TEAMS),
        key=len,
        reverse=True,
    )


def _strip_leading_team(label: str) -> tuple[str | None, str]:
    """若 label 以正式团队名开头，返回 (team, remainder)。"""
    s = (label or "").strip()
    if not s:
        return None, ""
    for team in _formal_teams():
        if s == team:
            return team, ""
        if s.startswith(team):
            rest = s[len(team):].lstrip(" ·•：:\t")
            return team, rest
    return None, s


def _is_attribution_tail(tail: str) -> bool:
    """remainder 是否为归属式业务后缀（而非自由正文）。"""
    t = (tail or "").strip()
    if not t:
        return True
    return bool(_ATTRIBUTION_SUFFIX.match(t))


def _neutral_attribution_tail(tail: str) -> str:
    """去掉错误团队前缀后，保留/归一业务语义。"""
    t = (tail or "").strip()
    if not t:
        return "团队记录"
    # 已是「建联记录」→ 加「团队」消歧
    if re.match(r"^(?:建联|沟通|见人|访谈|采访)记录", t):
        return f"团队{t}" if not t.startswith("团队") else t
    if t.startswith("记录"):
        return f"团队{t}"
    return t


def clean_source_label(label: str, owner_team: str) -> NarrativeCleanResult:
    """清理 source_label 的错误归属前缀；不修改 owner_team。"""
    original = (label or "").strip()
    owner = sanitize_owner_team(owner_team) or ""
    if not original:
        return NarrativeCleanResult(original, original, "keep", "空 label")
    if not owner:
        return NarrativeCleanResult(
            original, original, "needs_review", "缺少 owner_team，无法判断前缀", needs_review=True,
        )

    prefix_team, tail = _strip_leading_team(original)
    if prefix_team is None:
        # 团队名不在开头 → 可能是内容实体，不碰
        return NarrativeCleanResult(original, original, "keep", "无 leading 团队前缀")

    if prefix_team == owner:
        return NarrativeCleanResult(original, original, "keep", "前缀与 owner_team 一致")

    if _is_attribution_tail(tail):
        parts = re.split(r"\s*[·•]\s*", tail, maxsplit=1)
        attr_part = parts[0].strip()
        rest_part = parts[1].strip() if len(parts) > 1 else ""
        neutral = _neutral_attribution_tail(attr_part)
        cleaned = f"{neutral} · {rest_part}" if rest_part else neutral
        return NarrativeCleanResult(
            original,
            cleaned,
            "strip_prefix",
            f"剥离错误归属前缀 {prefix_team!r}（owner={owner!r}）",
        )

    # 开头像团队名但后续不是归属式后缀 → 可能是「硅谷 BD 合作方案」类内容
    return NarrativeCleanResult(
        original,
        original,
        "needs_review",
        f"leading {prefix_team!r} ≠ owner，但后缀像内容语义，不自动删",
        needs_review=True,
    )


def clean_detail_line(line: str, owner_team: str) -> NarrativeCleanResult:
    """清理 detail 行「X记录：」的错误团队宣称。"""
    original = (line or "").strip()
    owner = sanitize_owner_team(owner_team) or ""
    if not original:
        return NarrativeCleanResult(original, original, "keep", "空行")

    m = _DETAIL_ATTRIBUTION.match(original)
    if not m:
        return NarrativeCleanResult(original, original, "keep", "非「团队记录：」格式")

    detail_team = sanitize_owner_team(m.group(1).strip())
    body = m.group(2).strip()
    if not detail_team or detail_team == owner:
        return NarrativeCleanResult(original, original, "keep", "detail 团队与 owner 一致")

    # 展示清理：去掉错误团队，保留正文（中性「记录：」）
    cleaned = f"记录：{body}" if body else "记录"
    return NarrativeCleanResult(
        original,
        cleaned,
        "neutralize",
        f"detail 团队 {detail_team!r} ≠ owner {owner!r}，中性化展示",
    )


def audit_item_narrative(item: dict) -> list[NarrativeCleanResult]:
    """审计单条 item 的 narrative 字段（不写入）。"""
    owner = sanitize_owner_team(item.get("owner_team")) or ""
    out: list[NarrativeCleanResult] = []
    sl = (item.get("source_label") or "").strip()
    if sl:
        r = clean_source_label(sl, owner)
        if r.action != "keep":
            out.append(r)
    return out


def preview_issue_narrative(items: list[dict]) -> dict:
    """期号级 narrative 清理预览统计。"""
    stats = {"keep": 0, "strip_prefix": 0, "neutralize": 0, "needs_review": 0, "samples": []}
    for it in items:
        if int(it.get("blocked") or 0):
            continue
        owner = sanitize_owner_team(it.get("owner_team")) or ""
        r = clean_source_label(it.get("source_label") or "", owner)
        stats[r.action] = stats.get(r.action, 0) + 1
        if r.action != "keep" and len(stats["samples"]) < 30:
            stats["samples"].append({
                "item_id": it.get("id"),
                "owner_team": owner,
                "before": r.original,
                "after": r.cleaned,
                "action": r.action,
                "reason": r.reason,
            })
    return stats
