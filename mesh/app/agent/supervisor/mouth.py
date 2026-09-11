"""Single Mesh mouth — colleague voice; no protocol leakage."""
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
_TAG_RE = re.compile(r"\[(?:published|feishu_live|wiki_prior|analysis|system)/[^\]]+\]\s*")

_ERROR_UX = {
    "open_id_required_for_user": "查同事档案时缺必要身份标识，已改用通讯录姓名检索（或请你点名具体同事）。",
    "chat_id_required_for_members": "列群成员需要先选定一个群；你可以点一个群名让我继续。",
    "user_auth_required": "这项要你的个人飞书授权，授权后我就能继续查个人日历等。",
    "user_lookup_empty": "按身份没查到对应同事档案。",
    "empty": "这一侧暂时没有可读结果。",
    "not_found": "没有找到匹配项。",
    "hands_disabled": "飞书 Hands 未开启，飞书侧查不了。",
    "scope_denied": "当前权限不够，这一侧被拦住了。",
}


def sanitize(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    if _LEAK_RE.search(t):
        return "刚才组织答案时卡了一下，你再说一遍目标就行。"
    if _FAKE_HANDS.search(t) and "文档地址" not in t and "已写入" not in t:
        if "确认" in t and "预览" in t:
            return t
    return t


def _human_error(err: str, tool: str) -> str:
    e = (err or "").strip()
    if not e:
        return "这一步没跑通。"
    for k, v in _ERROR_UX.items():
        if k in e:
            return v
    if e.startswith("feishu_api_") or "feishu_api_" in e:
        return "飞书接口这一侧暂时失败，我先跳过。"
    # 绝不把裸错误码甩给同事
    return "这一侧暂时查不全，我先用已拿到的材料往下说。"


def _clean_snippet(text: str) -> str:
    t = _TAG_RE.sub("", (text or "").strip())
    t = re.sub(r"【已上线周报\s*[·•]\s*published】", "【已上线周报】", t)
    t = re.sub(r"【飞书\s*live\s*[·•]\s*feishu_live】", "【飞书侧】", t)
    return t.strip()


def format_columns(
    envelopes: list[TieredEnvelope],
    *,
    graph: TaskGraph,
    partial: bool,
    budget_hit: str,
) -> dict[str, str]:
    facts_pub: list[str] = []
    facts_live: list[str] = []
    facts_other: list[str] = []
    blockers: list[str] = []
    for r in envelopes:
        if not r.ok:
            blockers.append(_human_error(r.error, r.tool))
            continue
        snippet = _clean_snippet(r.text or "") or "有返回但摘要为空"
        # 去掉工具渲染里可能自带的技术前缀
        snippet = re.sub(r"^\[(?:published|feishu_live)/[^\]]+\]\s*", "", snippet)
        if r.tier == "published":
            facts_pub.append(snippet[:900])
        elif r.tier == "feishu_live":
            label = {
                "org": "组织/群",
                "calendar": "日历",
                "research": "飞书资料",
            }.get(r.worker, "飞书")
            facts_live.append(f"（{label}）{snippet[:900]}")
        else:
            facts_other.append(snippet[:600])

    fact_parts: list[str] = []
    if facts_pub:
        fact_parts.append("【已上线周报】\n" + "\n".join(facts_pub[:4]))
    if facts_live:
        fact_parts.append("【飞书侧】\n" + "\n".join(facts_live[:5]))
    if facts_other:
        fact_parts.append("【其他】\n" + "\n".join(facts_other[:2]))
    if not fact_parts:
        fact_parts.append("这轮还没拿到能直接引用的材料（可能是权限、授权或结果为空）。")
    if blockers:
        # 去重保序
        seen: set[str] = set()
        uniq = []
        for b in blockers:
            if b not in seen:
                seen.add(b)
                uniq.append(b)
        fact_parts.append("【这轮没查全的地方】\n" + "\n".join(f"- {b}" for b in uniq[:5]))

    analysis_bits: list[str] = []
    if facts_pub and facts_live:
        analysis_bits.append("周报和飞书侧我分开列了，不会把群聊讨论写成「周报里记过」。")
    if graph.band == "complex":
        analysis_bits.append("多源任务我已尽量对齐；哪些最值得盯，还要结合你手头在推的事。")
    if budget_hit:
        analysis_bits.append("时间/步数预算到了，下面是已完成步骤的结果；你可以指定一个群或人名让我继续收窄。")
    elif partial and blockers:
        analysis_bits.append("有几步没跑通，我按已拿到的材料先给你一版，缺的可以点名让我补。")
    analysis = "\n".join(analysis_bits) if analysis_bits else "材料有限，关联还偏弱。"

    opinion = (
        "我会先盯「飞书协作里出现、周报里也有进展」的交叉项；只有单侧信号的先放一放。"
        if (facts_pub and facts_live)
        else "交叉还不够，我暂时不硬排序。"
    )
    suggestion = (
        "你可以：①补个人日历授权；②点一个具体群名或人名，我把成员/日程/周报再收一版。"
        if graph.band == "complex"
        else "要继续深挖的话，直接丢人名或项目别名就行。"
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
    return sanitize("\n\n".join(blocks).strip())


def speak(
    user_text: str,
    identity: Any,
    session: Any,
    *,
    company_block: str = "",
    hint: str = "",
) -> tuple[str, dict[str, Any]]:
    from .. import colleague_v3 as v3

    text, meta = v3._speak_plain(user_text, identity, session, company_block=company_block)
    if hint and len((text or "").strip()) < 8:
        text = hint
    return sanitize(text), meta
