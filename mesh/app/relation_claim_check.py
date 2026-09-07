"""Relation Claim Check：Writer 原文论断强度守卫（非 lexical grounding）。

检查 title + body + details；与 Evidence Gate / line_grounded 独立。
默认 shadow（只写 audit），MESH_CLAIM_CHECK_MODE=enforce 才藏卡。

rule_v1.1（A1.1）：
- watch/one_sided 的 CONTACT 不因 evidence 缺同词误杀（缺词 ≠ 否定）
- blocker 不按整卡全局下压；仅在完成态论断与未完成证据冲突时生效
"""
from __future__ import annotations

import os
from typing import Any

# 强度阶梯：提及 < 关注 < 沟通 < 讨论 < 计划 < 推进 < 达成/合作 < 签约/上线/落地
STRENGTH_MENTION = 1
STRENGTH_WATCH = 2
STRENGTH_CONTACT = 3
STRENGTH_DISCUSS = 4
STRENGTH_PLAN = 5
STRENGTH_PUSH = 6
STRENGTH_DEAL = 7
STRENGTH_SIGNED = 8

_MODEL = "rule_v1.1"

# (level, patterns) — 长词优先靠列表顺序 + 扫描时取 max
_STRENGTH_PATTERNS: list[tuple[int, tuple[str, ...]]] = [
    (
        STRENGTH_SIGNED,
        (
            "正式签署",
            "签署合作",
            "签订协议",
            "签约",
            "已上线",
            "完成上线",
            "已落地",
            "完成落地",
            "落地执行",
            "联合签约",
            "正式合作协议",
        ),
    ),
    (
        STRENGTH_DEAL,
        (
            "达成正式合作",
            "达成合作协议",
            "达成合作",
            "已形成合作",
            "形成合作",
            "统一商务合作",
            "统一方案",
            "联合合作",
            "联动合作",
            "三方联动",
            "联合活动",
            "联合推进",
            "联合执行",
            "已完成合作",
            "完成联动",
            "合作协议",
            "商务合作推进",
            "合作推进",
        ),
    ),
    (
        STRENGTH_PUSH,
        (
            "正在推进",
            "联合推进",
            "推进中",
            "已推进",
            "进入推进",
        ),
    ),
    (
        STRENGTH_PLAN,
        (
            "仍在商量",
            "商量中",
            "待明确",
            "计划两周",
            "计划参加",
            "计划探讨",
            "尚未排期",
            "计划",
            "打算",
            "拟定",
        ),
    ),
    (
        STRENGTH_DISCUSS,
        (
            "内部讨论",
            "讨论是否",
            "沟通了后续",
            "探讨",
            "商量",
            "讨论",
        ),
    ),
    (
        STRENGTH_CONTACT,
        (
            "已接触",
            "在接触",
            "正在联系",
            "通过 PR",
            "联系同一人",
            "出镜",
            "沟通对象",
            "接触",
            "跟进",
            "沟通",
            "建联",
        ),
    ),
    (
        STRENGTH_WATCH,
        (
            "关注",
            "观察",
            "复盘",
            "选题",
            "排期含",
            "行业判断",
            "非核实事实",
        ),
    ),
    (
        STRENGTH_MENTION,
        (
            "各知一半",
            "不同触点",
            "平行",
            "提及",
            "提到",
            "相关",
        ),
    ),
]

# 仅「未完成 / 否定」类；作用于对应完成态 claim，不做整卡全局下压
_COMPLETION_BLOCKERS: tuple[str, ...] = (
    "尚无反馈",
    "尚未排期",
    "尚未接触",
    "未接触",
    "没有接触",
    "未建联",
    "尚未建联",
    "仍在商量",
    "待明确",
    "商量中",
    "未直接",
)

_CONTACT_DENIALS: tuple[str, ...] = (
    "尚未接触",
    "未接触",
    "没有接触",
    "未建联",
    "尚未建联",
    "尚未跟进",
)

_REASON_OVERCLAIM = "status_upgrade"
_REASON_NEGATION = "evidence_blocker"
_REASON_COOCCUR = "cooccurrence_not_relation"
_REASON_OK = "ok"
_REASON_EMPTY = "empty_claim"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"


def claim_check_mode(override: str | None = None) -> str:
    raw = (override if override is not None else os.environ.get("MESH_CLAIM_CHECK_MODE", MODE_SHADOW))
    m = (raw or MODE_SHADOW).strip().lower()
    if m in (MODE_OFF, MODE_SHADOW, MODE_ENFORCE):
        return m
    return MODE_SHADOW


def max_strength(text: str) -> int:
    s = text or ""
    if not s.strip():
        return 0
    best = 0
    for level, pats in _STRENGTH_PATTERNS:
        for p in pats:
            if p in s:
                if level > best:
                    best = level
                break
    return best


def evidence_effective_strength(blob: str) -> tuple[int, int | None]:
    """兼容旧调用：返回 (raw_max_strength, None)。

    A1.1 起不再用 blocker 全局下压；blocker 在 check_relation_claim 内按 claim 应用。
    """
    return max_strength(blob), None


def evidence_blob(rel: dict, items: list[dict] | None = None) -> str:
    return "\n".join(_evidence_pieces(rel, items))


def _evidence_pieces(rel: dict, items: list[dict] | None = None) -> list[str]:
    parts: list[str] = []
    by_id: dict[Any, dict] = {}
    for it in items or []:
        if isinstance(it, dict) and it.get("id") is not None:
            by_id[it["id"]] = it
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        chunk: list[str] = []
        for k in ("snippet", "quote", "team", "pointer"):
            v = e.get(k)
            if v:
                chunk.append(str(v))
        iid = e.get("item_id")
        row = by_id.get(iid)
        if row and row.get("text"):
            chunk.append(str(row["text"]))
        if chunk:
            parts.append("\n".join(chunk))
    return parts


def _has_evidence(rel: dict) -> bool:
    return any(isinstance(e, dict) for e in (rel.get("evidence") or []))


def _watchish(rel: dict) -> bool:
    if rel.get("weak") or rel.get("decision_tier") == "watch":
        return True
    rt = (rel.get("relation_type") or "").strip()
    if rt in ("one_sided", "overseas_link"):
        return True
    for t in rel.get("teams") or []:
        if str(t).strip().startswith("→"):
            return True
    return False


def _any_piece_supports_level(rel: dict, items: list[dict] | None, level: int) -> bool:
    if level <= 0:
        return True
    for piece in _evidence_pieces(rel, items):
        if max_strength(piece) >= level:
            return True
    return False


def _blob_has_any(blob: str, phrases: tuple[str, ...]) -> bool:
    return any(p in (blob or "") for p in phrases)


def _spans(rel: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    title = (rel.get("title") or "").strip()
    if title:
        out.append(("title", title))
    body = (rel.get("body") or "").strip()
    if body:
        out.append(("body", body))
    for i, d in enumerate(rel.get("details") or []):
        ds = str(d or "").strip()
        if ds:
            out.append((f"detail:{i}", ds))
    return out


def _looks_parallel_context(rel: dict) -> bool:
    rt = (rel.get("relation_type") or "").strip()
    if rt in ("parallel_tracks", "info_complement"):
        return True
    label = (rel.get("label") or "") + (rel.get("title") or "") + (rel.get("body") or "")
    return any(h in label for h in ("各知一半", "不同触点", "平行", "同一公司"))


def check_relation_claim(
    rel: dict,
    items: list[dict] | None = None,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """对单张 Writer 原文卡做 Claim Check。不改写 title/body/details。"""
    model = model or _MODEL
    spans = _spans(rel)
    blob = evidence_blob(rel, items)
    ev_strength = max_strength(blob)

    result: dict[str, Any] = {
        "claim_verdict": "valid",
        "claim_reason_code": _REASON_OK,
        "claim_reason": "论断强度未超过证据",
        "checked_span": [s[0] for s in spans],
        "claim_strength": 0,
        "evidence_strength": ev_strength,
        "blocker_cap": None,
        "model": model,
        "prompt_version": None,
    }
    if not spans:
        result["claim_verdict"] = "uncertain"
        result["claim_reason_code"] = _REASON_EMPTY
        result["claim_reason"] = "无可检查的 title/body/details"
        return result

    worst: dict[str, Any] | None = None
    max_claim = 0
    hit_spans: list[str] = []

    for span_name, text in spans:
        cs = max_strength(text)
        if cs > max_claim:
            max_claim = cs

        # 任一条 evidence 已支撑该强度 → OK（计划不被另一条「尚无反馈」全局压掉）
        if _any_piece_supports_level(rel, items, cs):
            continue

        # A1.1：watch/one_sided + 有效 evidence + 仅 CONTACT/建联
        # 「缺接触字面」≠「接触不成立」；有明确否定才拦
        if (
            cs <= STRENGTH_CONTACT
            and _watchish(rel)
            and _has_evidence(rel)
            and not _blob_has_any(blob, _CONTACT_DENIALS)
        ):
            continue

        if cs <= ev_strength:
            continue

        # 论断强于整卡 max，且无单条 evidence 支撑
        code = _REASON_OVERCLAIM
        reason = f"{span_name} 论断强度 {cs} > 证据有效强度 {ev_strength}"
        # 完成态论断 + 未完成/否定证据 → blocker（只打完成态，不打「计划」）
        if cs >= STRENGTH_PUSH and _blob_has_any(blob, _COMPLETION_BLOCKERS):
            code = _REASON_NEGATION
            reason = (
                f"{span_name} 完成态强度 {cs}，但证据含未完成/否定标记"
            )
        if _looks_parallel_context(rel) and cs >= STRENGTH_DEAL:
            code = _REASON_COOCCUR
            reason = f"{span_name} 在平行/各知一半语境下升级为合作级论断"
        hit_spans.append(span_name)
        cand = {
            "claim_verdict": "invalid",
            "claim_reason_code": code,
            "claim_reason": reason,
            "checked_span": [span_name],
            "claim_strength": cs,
            "evidence_strength": ev_strength,
            "blocker_cap": None,
            "model": model,
            "prompt_version": None,
        }
        if worst is None or cs >= (worst.get("claim_strength") or 0):
            worst = cand

    result["claim_strength"] = max_claim
    if worst is not None:
        worst["checked_span"] = hit_spans or worst["checked_span"]
        return worst

    # watch/weak：正文/标题若出现双边完成态，仍 invalid
    if (rel.get("weak") or rel.get("decision_tier") == "watch") and max_claim >= STRENGTH_DEAL:
        return {
            "claim_verdict": "invalid",
            "claim_reason_code": _REASON_OVERCLAIM,
            "claim_reason": "虚线/观察卡出现合作完成态论断",
            "checked_span": [s[0] for s in spans],
            "claim_strength": max_claim,
            "evidence_strength": ev_strength,
            "blocker_cap": None,
            "model": model,
            "prompt_version": None,
        }

    return result


def snapshot_writer_raw(rel: dict) -> dict[str, Any]:
    return {
        "title": rel.get("title"),
        "body": rel.get("body"),
        "details": list(rel.get("details") or []),
    }


def apply_claim_checks(
    relations: list[dict],
    items: list[dict] | None = None,
    *,
    mode: str | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """对 Writer 原文批量 Claim Check。

    shadow：保留全部卡，只写 _claim_check / audit。
    enforce：去掉 claim_verdict==invalid 的卡。
    off：原样返回。
    """
    m = claim_check_mode(mode)
    audit: dict[str, Any] = {
        "mode": m,
        "model": _MODEL,
        "n_checked": 0,
        "n_valid": 0,
        "n_invalid": 0,
        "n_uncertain": 0,
        "n_dropped_enforce": 0,
        "by_reason": {},
        "rows": [],
    }
    if m == MODE_OFF:
        return list(relations or []), audit

    kept: list[dict] = []
    for rel in relations or []:
        if not isinstance(rel, dict):
            continue
        row = dict(rel)
        raw = row.get("_writer_raw")
        if not isinstance(raw, dict):
            raw = snapshot_writer_raw(row)
            row["_writer_raw"] = raw
        probe = dict(row)
        probe["title"] = raw.get("title")
        probe["body"] = raw.get("body")
        probe["details"] = raw.get("details")
        check = check_relation_claim(probe, items)
        row["_claim_check"] = check
        audit["n_checked"] += 1
        verdict = check.get("claim_verdict") or "uncertain"
        if verdict == "valid":
            audit["n_valid"] += 1
        elif verdict == "invalid":
            audit["n_invalid"] += 1
        else:
            audit["n_uncertain"] += 1
        code = check.get("claim_reason_code") or ""
        audit["by_reason"][code] = audit["by_reason"].get(code, 0) + 1
        audit["rows"].append(
            {
                "candidate_id": row.get("candidate_id"),
                "title": (raw.get("title") or "")[:120],
                "claim_verdict": verdict,
                "claim_reason_code": code,
                "claim_reason": check.get("claim_reason"),
                "claim_strength": check.get("claim_strength"),
                "evidence_strength": check.get("evidence_strength"),
            }
        )
        if m == MODE_ENFORCE and verdict == "invalid":
            audit["n_dropped_enforce"] += 1
            continue
        kept.append(row)

    if m == MODE_ENFORCE and audit["n_dropped_enforce"]:
        audit["dropped_titles"] = [
            r["title"] for r in audit["rows"] if r.get("claim_verdict") == "invalid"
        ]
    return kept, audit
