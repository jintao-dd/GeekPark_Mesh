"""Single Mesh mouth — only Supervisor synthesizes user-facing text."""
from __future__ import annotations

import logging
import re
from typing import Any

from .types import TaskGraph, TieredEnvelope

log = logging.getLogger("uvicorn.error")

_LEAK_RE = re.compile(r"""^\s*[\{\[]\s*['"](?:action|mode)['"]\s*:""", re.I)
_FAKE_HANDS = re.compile(
    r"(已创建|正在创建|创建成功|写入成功|已经写进飞书|已发到飞书|日程已建好)",
    re.I,
)


def sanitize(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    if _LEAK_RE.search(t):
        return "刚才组织答案时卡了一下，你再说一遍目标就行。"
    if _FAKE_HANDS.search(t) and "文档地址" not in t and "已写入" not in t:
        # 无明确写入成功痕迹时拦截谎称
        if "确认" in t and "预览" in t:
            return t
    return t


def format_columns(envelopes: list[TieredEnvelope], *, graph: TaskGraph, partial: bool, budget_hit: str) -> dict[str, str]:
    facts_pub: list[str] = []
    facts_live: list[str] = []
    facts_other: list[str] = []
    errors: list[str] = []
    for r in envelopes:
        if not r.ok:
            errors.append(f"{r.tool}: {r.error or '未成功'}")
            continue
        snippet = (r.text or "").strip() or f"{r.tool} 有返回但无可读摘要"
        line = f"[{r.tier}/{r.worker}] {snippet[:800]}"
        if r.tier == "published":
            facts_pub.append(line)
        elif r.tier == "feishu_live":
            facts_live.append(line)
        else:
            facts_other.append(line)

    fact_parts: list[str] = []
    if facts_pub:
        fact_parts.append("【已上线周报 · published】\n" + "\n".join(facts_pub[:4]))
    if facts_live:
        fact_parts.append("【飞书 live · feishu_live】\n" + "\n".join(facts_live[:4]))
    if facts_other:
        fact_parts.append("【其他】\n" + "\n".join(facts_other[:2]))
    if not fact_parts:
        fact_parts.append("（本轮未拿到可引用事实；可能权限/授权/空结果。）")
    if errors:
        fact_parts.append("【受阻】\n" + "\n".join(errors[:6]))

    analysis_bits: list[str] = []
    if facts_pub and facts_live:
        analysis_bits.append(
            "周报与飞书 live 分桶列出：不把飞书讨论写成「周报里记录」。"
        )
    if graph.band == "complex":
        analysis_bits.append("多源任务：相关性需结合你的当前工作判断；我只对齐材料。")
    if partial:
        analysis_bits.append(f"任务部分完成（budget={budget_hit or 'skipped'}）。")
    analysis = "\n".join(analysis_bits) if analysis_bits else "材料有限，关联强度偏弱。"

    opinion = (
        "优先盯「飞书协作 ∩ 周报进展」交叉项；单侧信号先放一放。"
        if (facts_pub and facts_live)
        else "材料还不够交叉，我暂时不硬给排序结论。"
    )
    suggestion = (
        "下一步：补个人日历授权，或指定一个群/人名，我再收窄一版。"
        if graph.band == "complex"
        else "若要继续深挖，直接丢人名或项目别名即可。"
    )
    return {
        "FACT": "\n\n".join(fact_parts),
        "ANALYSIS": analysis,
        "OPINION": opinion,
        "SUGGESTION": suggestion,
    }


def render_work_answer(columns: dict[str, str]) -> str:
    labels = {
        "FACT": "我查到的",
        "ANALYSIS": "怎么串起来看",
        "OPINION": "我的判断",
        "SUGGESTION": "建议下一步",
    }
    blocks: list[str] = ["按你的目标，我分几块说："]
    for k in ("FACT", "ANALYSIS", "OPINION", "SUGGESTION"):
        v = (columns.get(k) or "").strip()
        if v:
            blocks.append(f"**{labels[k]}**\n{v}")
    return "\n\n".join(blocks).strip()


def speak(
    user_text: str,
    identity: Any,
    session: Any,
    *,
    company_block: str = "",
    hint: str = "",
) -> tuple[str, dict[str, Any]]:
    from .. import colleague_v3 as v3

    # 复用既有口吻与安全裁剪，避免双嘴分叉；对外仍只经 Supervisor 出口
    text, meta = v3._speak_plain(user_text, identity, session, company_block=company_block)
    if hint and len((text or "").strip()) < 8:
        text = hint
    return sanitize(text), meta
