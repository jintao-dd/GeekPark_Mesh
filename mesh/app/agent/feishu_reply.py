"""Feishu Agent v1 · 用户可见回复格式。

核心原则：可验证事实回答必须带 Claim 对齐的 Evidence / 期次引用。
Failure UX：no_hit / insufficient / permission / system_error 分腔，禁止统一 no_evidence。
不改 Retrieval / Ranking / Claim Support 判定本身。
"""
from __future__ import annotations

import re
from typing import Any

from .models import AgentAnswer, ClaimBinding

_NO_HIT_MARKERS = (
    "未在已上线周报中找到与问题直接相关的记录",
    "未在已上线语料中找到可引用依据",
    "我目前没查到已发布的内容能确认这件事",
)

_NO_HIT_UX = "这期周报里我没找到能直接回答的内容。你可以换个人名/公司名，或换个说法再问一次。"
_INSUFFICIENT_UX = (
    "有相关记录，但还不足以证明你问的那一点。"
    "可以再具体一点（人名、公司，或问是哪一期）。"
)
_CONTRADICTED_UX = "周报里有和这个说法不太一致的记录，我先不按原说法下结论。"
_SYSTEM_UX = "刚才没查顺，你再发一次我就好。"
_TIMEOUT_UX = "这次有点慢，我这边超时了。你再发一次，或把问题缩短一点。"
_PERMISSION_UX = "这部分信息不在你当前可查看的范围内。"


def _issue_slug(answer: AgentAnswer, payload: dict[str, Any] | None = None) -> str:
    p = payload or {}
    if p.get("issue"):
        return str(p["issue"]).strip()
    ctx = answer.context or {}
    iref = ctx.get("issue_ref") or {}
    if isinstance(iref, dict) and iref.get("slug"):
        return str(iref["slug"]).strip()
    return ""


def _support_label(answer: AgentAnswer, payload: dict[str, Any] | None = None) -> tuple[str, str]:
    p = payload or {}
    cs = p.get("claim_support") or answer.trace.get("claim_support") or {}
    if isinstance(cs, dict):
        return str(cs.get("support") or "").strip(), str(cs.get("reason") or "").strip()
    for b in answer.claim_bindings or []:
        if b.status == "unsupported":
            return "insufficient", b.reason or ""
        if b.status == "grounded":
            return "supported", b.reason or ""
        if b.status == "weak":
            return "insufficient", b.reason or ""
    return "", ""


def _is_orchestrated(answer: AgentAnswer, payload: dict[str, Any] | None) -> bool:
    """多源编排 / Hands 成文：禁止被 Published no_hit 模板整段替换。"""
    tr = answer.trace or {}
    if isinstance(tr.get("orchestrator"), dict):
        return True
    p = payload or {}
    if isinstance(p.get("columns"), dict) and p.get("columns"):
        return True
    if str(p.get("complexity") or "").lower() in ("complex", "medium"):
        return True
    tier = str(p.get("source_tier") or tr.get("source_tier") or "").lower()
    text = answer.text or ""
    if tier == "feishu_live" and (
        "我查到的" in text or "飞书 live" in text or "按你的目标" in text
    ):
        return True
    return False


def _failure_kind(
    answer: AgentAnswer,
    payload: dict[str, Any] | None,
    support: str,
    reason: str,
) -> str:
    """no_hit | insufficient | contradicted | permission | system_error | timeout | ok"""
    if answer.refused or answer.intent == "refuse":
        dr = (answer.deny_reason or "").lower()
        if "acl" in dr or "permission" in dr or "identity" in dr:
            return "permission"
        return "permission" if "无权" in (answer.text or "") else "ok"
    if answer.intent in ("help", "whoami", "casual", "clarify", "list_issues"):
        return "ok"
    # Orchestrator / 多源 Hands：即使某一步 Published 空，也不能整段盖成周报 no_hit
    if _is_orchestrated(answer, payload):
        body = (answer.text or "").strip()
        if "超时" in body or "timeout" in body.lower():
            return "timeout"
        return "ok"
    if answer.intent in (
        "feishu_search",
        "feishu_doc_get",
        "feishu_calendar_list",
        "feishu_calendar_propose",
        "feishu_discuss",
        "feishu_write",
    ):
        body = (answer.text or "").strip()
        if "超时" in body or "timeout" in body.lower():
            return "timeout"
        # Hands 单工具：不要用周报 no_hit 话术
        if any(m in body for m in _NO_HIT_MARKERS):
            return "ok"
        return "ok"
    body = (answer.text or "").strip()
    if "超时" in body or "timeout" in body.lower() or reason == "timeout":
        return "timeout"
    if any(m in body for m in _NO_HIT_MARKERS) or reason in ("no_evidence", "no_hit", "empty"):
        # n_hits==0 更像 no_hit
        n_hits = (payload or {}).get("n_hits")
        if n_hits == 0 or support in ("", "insufficient", "no_evidence") or any(
            m in body for m in _NO_HIT_MARKERS
        ):
            if support == "contradicted":
                return "contradicted"
            if n_hits and int(n_hits) > 0 and support == "insufficient":
                return "insufficient"
            if any(m in body for m in _NO_HIT_MARKERS) and (not n_hits or int(n_hits or 0) == 0):
                return "no_hit"
            if support == "insufficient":
                return "insufficient"
            return "no_hit"
    if support == "contradicted":
        return "contradicted"
    if support == "insufficient":
        return "insufficient"
    if "没查成功" in body or "再试一次" in body:
        return "system_error"
    return "ok"


def _humanize_ref(ref: str) -> str:
    r = (ref or "").strip()
    if not r:
        return ""
    if r.startswith("crm:stats:"):
        parts = r.split(":")
        return f"CRM统计 {parts[2] if len(parts) > 2 else ''}={parts[3] if len(parts) > 3 else ''}".strip()
    if r.startswith("crm:cross:"):
        return "CRM×周报交叉"
    if r.startswith("crm:"):
        return r.replace("crm:", "CRM·", 1)[:48]
    m = re.search(r"item[:/]?(\d+)", r, re.I)
    if m:
        return f"条目 {m.group(1)}"
    return r


def _rewrite_body_for_failure(body: str, kind: str) -> str:
    if kind == "no_hit":
        # 去掉机械检索提示尾巴，换人话
        if any(m in body for m in _NO_HIT_MARKERS) or len(body) < 80:
            return _NO_HIT_UX
        return body
    if kind == "insufficient":
        if any(m in body for m in _NO_HIT_MARKERS) or "依据不足" in body:
            return _INSUFFICIENT_UX
        # 保留模型已写的谨慎结论，不叠模板
        return body
    if kind == "contradicted":
        if any(m in body for m in _NO_HIT_MARKERS):
            return _CONTRADICTED_UX
        return body
    if kind == "system_error":
        return _SYSTEM_UX
    if kind == "timeout":
        return _TIMEOUT_UX
    if kind == "permission":
        return body or _PERMISSION_UX
    return body


def format_display_text(
    answer: AgentAnswer,
    *,
    payload: dict[str, Any] | None = None,
) -> str:
    """组装飞书/产品侧可见正文：Answer + 期次 + Claim 对齐证据。"""
    body = (answer.text or "").strip()
    if answer.refused and answer.intent == "refuse":
        return body

    if answer.intent in ("help", "whoami", "casual", "clarify", "list_issues"):
        return body

    issue = _issue_slug(answer, payload)
    support, reason = _support_label(answer, payload)
    kind = _failure_kind(answer, payload, support, reason)
    body = _rewrite_body_for_failure(body, kind)

    refs = list(answer.evidence_refs or [])
    if not refs:
        for b in answer.claim_bindings or []:
            refs.extend(list(b.evidence_refs or []))
    seen: set[str] = set()
    uniq_refs: list[str] = []
    for r in refs:
        if r and r not in seen:
            seen.add(r)
            uniq_refs.append(r)

    blocks: list[str] = [body]
    meta_lines: list[str] = []

    # no_hit / system_error / timeout：只留人话，不甩内部字段
    if kind in ("no_hit", "system_error", "timeout", "permission"):
        return body

    # feishu_live Hands 回答禁止刷「已上线周报」归因
    tier = ""
    if isinstance(payload, dict):
        tier = str(payload.get("source_tier") or "").strip().lower()
    if not tier:
        tier = str((answer.trace or {}).get("source_tier") or "").strip().lower()
    if not tier and str(getattr(answer, "intent", "") or "") in (
        "feishu_search",
        "feishu_doc_get",
        "feishu_calendar_list",
        "feishu_calendar_propose",
        "feishu_discuss",
        "feishu_write",
    ):
        tier = "feishu_live"
    if tier == "feishu_live":
        if uniq_refs and kind not in ("no_hit", "system_error", "timeout", "permission"):
            meta_lines.append("可核对：")
            for r in uniq_refs[:6]:
                meta_lines.append(f"· {_humanize_ref(r)}")
        if meta_lines:
            blocks.append("")
            blocks.append("——")
            blocks.extend(meta_lines)
            return "\n".join(blocks).strip()
        return body

    # CRM 回答禁止刷「已上线周报」归因
    crmish = tier == "crm_prior" or any(str(r).startswith("crm:") for r in uniq_refs)
    if not crmish and str(getattr(answer, "intent", "") or "") in (
        "crm_search",
        "crm_stats",
    ):
        crmish = True
    if not crmish and isinstance(payload, dict):
        tools = payload.get("tools_called") or (answer.tools_called if hasattr(answer, "tools_called") else [])
        if any(str(t) == "crm.search" for t in (tools or [])):
            crmish = True
    if crmish:
        cross_op = ""
        if isinstance(payload, dict):
            cross_op = str(payload.get("cross_op") or "").strip()
        if not cross_op:
            cross_op = str((answer.trace or {}).get("cross_op") or "").strip()
        if cross_op:
            meta_lines.append("来源：硅谷 CRM × 已上线周报（分栏对照）")
        else:
            meta_lines.append("来源：硅谷 CRM（Notion）")
        if uniq_refs and kind not in ("no_hit", "system_error", "timeout", "permission"):
            meta_lines.append("可核对：")
            for r in uniq_refs[:6]:
                meta_lines.append(f"· {_humanize_ref(r)}")
        if meta_lines:
            blocks.append("")
            blocks.append("——")
            blocks.extend(meta_lines)
        return "\n".join(blocks).strip()

    if issue and kind in ("ok", "supported", ""):
        meta_lines.append(f"来源：{issue} 已上线周报")
    elif issue and kind not in ("",):
        meta_lines.append(f"来源期次：{issue}")

    if kind == "insufficient":
        meta_lines.append("目前能确认的有限：有相关记录，但还不足以证明那一点")
    elif kind == "contradicted":
        meta_lines.append("说明：周报里有不一致记录")

    if uniq_refs and kind not in ("no_hit", "system_error", "timeout", "permission"):
        meta_lines.append("可核对：")
        for r in uniq_refs[:6]:
            meta_lines.append(f"· {_humanize_ref(r)}")

    if meta_lines:
        blocks.append("")
        blocks.append("——")
        blocks.extend(meta_lines)

    return "\n".join(blocks).strip()


def enrich_answer_for_display(
    answer: AgentAnswer,
    *,
    payload: dict[str, Any] | None = None,
) -> AgentAnswer:
    """写入 trace.display_text；不改大脑判定。"""
    display = format_display_text(answer, payload=payload)
    answer.trace = dict(answer.trace or {})
    answer.trace["display_text"] = display
    support, reason = _support_label(answer, payload)
    kind = _failure_kind(answer, payload, support, reason)
    answer.trace["failure_kind"] = kind
    if payload and payload.get("claim_support"):
        answer.trace["claim_support"] = payload.get("claim_support")
    if payload and payload.get("issue"):
        answer.trace["issue"] = payload.get("issue")
    return answer


def claim_bindings_summary(bindings: list[ClaimBinding]) -> list[dict[str, Any]]:
    return [b.to_dict() for b in (bindings or [])]
