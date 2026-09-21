"""Single Mesh mouth — preserve user intent; do not truncate model answers lightly."""
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
_TAG_RE = re.compile(
    r"\[(?:published|feishu_live|crm_prior|wiki_prior|analysis|system)/[^\]]+\]\s*"
)

# 成文材料上限：宁多勿砍；飞书卡片另有展示上限
_MATERIAL_CAP = 14000
_SNIPPET_CAP = 6000

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

_SYNTH_SYSTEM = """你是 GeekPark 内部同事 Mesh（唯一对外的一张嘴）。

任务：按用户的**完整原话目标**，把下面分桶材料组织成完整、可执行的同事答复。

硬规则：
1) 不得弱化、改写用户目标；用户问了什么就答什么，材料不够就明说缺哪一块，不要假装答完。
2) published = 已上线周报事实；feishu_live = 飞书现场；crm_prior = 硅谷 CRM（思琪/Lilyann Notion 跟进），禁止把 CRM/飞书说成「周报里记录」。
3) 不要输出 JSON、不要输出 action/tool、不要甩 open_id/chat_id/budget 等协议词。
4) 材料里已有的人名、群、日程、周报条目必须尽量完整转述，禁止无故截短成口号。
5) 可用结构：我查到的 / 怎么串起来看 / 我的判断 / 建议下一步——但内容要充实，不要套话。
6) 禁止谎称已写入飞书。
7) 「我们团队 / 和我们相关」按材料里「提问者直属部门同事」来认，不按周报桶名，也不把同级部门并进来。
   某人材料里已有「部门:」就据实说他在哪个部门，禁止说成缺部门。
   周报「硅谷 BD 团队」只是内容标签；只有直属部门名单里的人才算团队相关。
   若名单里有海外拓展的赵思琪/Sean Shen/胡清远，即使条目挂在「硅谷 BD」下也要纳入，禁止说成「不属于品牌创意」。
8) 用户问「相关 / 周报 / 进展 / 和我有关」时：以已上线周报与人名进展为主答案；
   日历忙碌/空闲时段只作补充一句，禁止把日程列表当主答案（除非用户明确问会议/日程/忙不忙）。
"""


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
    return "这一侧暂时查不全，我先用已拿到的材料往下说。"


def _clean_snippet(text: str) -> str:
    t = _TAG_RE.sub("", (text or "").strip())
    t = re.sub(r"【已上线周报\s*[·•]\s*published】", "【已上线周报】", t)
    t = re.sub(r"【飞书\s*live\s*[·•]\s*feishu_live】", "【飞书侧】", t)
    t = re.sub(r"\s*[—\-]\s*ou_[a-zA-Z0-9]+", "", t)
    t = re.sub(r"\bou_[a-zA-Z0-9]+\b", "", t)
    t = re.sub(r"\boc_[a-zA-Z0-9]+\b", "", t)
    t = re.sub(r"[（(]\s*[）)]", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def materials_block(envelopes: list[TieredEnvelope]) -> str:
    """拼完整材料给 LLM；只做展示清洗，不做语义弱化。"""
    parts: list[str] = []
    blockers: list[str] = []
    for r in envelopes:
        if not r.ok:
            blockers.append(_human_error(r.error, r.tool))
            continue
        snippet = _clean_snippet(r.text or "")
        if not snippet:
            continue
        if len(snippet) > _SNIPPET_CAP:
            snippet = snippet[: _SNIPPET_CAP - 20] + "\n…（单条材料过长，已保留前段）"
        bucket = {
            "published": "已上线周报",
            "feishu_live": "飞书侧",
            "crm_prior": "硅谷 CRM（思琪侧）",
        }.get(r.tier, r.tier or "其他")
        worker = r.worker or ""
        parts.append(f"### {bucket}" + (f" · {worker}" if worker else "") + f"\n{snippet}")
    if blockers:
        seen: set[str] = set()
        uniq = []
        for b in blockers:
            if b not in seen:
                seen.add(b)
                uniq.append(b)
        parts.append("### 这轮没查全的地方\n" + "\n".join(f"- {b}" for b in uniq[:8]))
    body = "\n\n".join(parts).strip()
    if len(body) > _MATERIAL_CAP:
        body = body[: _MATERIAL_CAP - 40] + "\n\n…（材料总量过大，已保留前段完整内容）"
    return body or "（本轮没有可用材料）"


def format_columns(
    envelopes: list[TieredEnvelope],
    *,
    graph: TaskGraph,
    partial: bool,
    budget_hit: str,
) -> dict[str, str]:
    """规则分栏：仅作 LLM 失败兜底 / trace，不再当默认成文。"""
    facts_pub: list[str] = []
    facts_live: list[str] = []
    facts_other: list[str] = []
    blockers: list[str] = []
    for r in envelopes:
        if not r.ok:
            blockers.append(_human_error(r.error, r.tool))
            continue
        snippet = _clean_snippet(r.text or "") or "有返回但摘要为空"
        snippet = re.sub(r"^\[(?:published|feishu_live)/[^\]]+\]\s*", "", snippet)
        if len(snippet) > _SNIPPET_CAP:
            snippet = snippet[:_SNIPPET_CAP]
        if r.tier == "published":
            facts_pub.append(snippet)
        elif r.tier == "feishu_live":
            label = {
                "org": "组织/群",
                "calendar": "日历",
                "research": "飞书资料",
            }.get(r.worker, "飞书")
            facts_live.append(f"（{label}）{snippet}")
        elif r.tier == "crm_prior":
            facts_other.append(f"（硅谷CRM）{snippet}")
        else:
            facts_other.append(snippet)

    fact_parts: list[str] = []
    if facts_pub:
        fact_parts.append("【已上线周报】\n" + "\n".join(facts_pub[:8]))
    if facts_live:
        fact_parts.append("【飞书侧】\n" + "\n".join(facts_live[:10]))
    if facts_other:
        fact_parts.append("【其他】\n" + "\n".join(facts_other[:4]))
    if not fact_parts:
        fact_parts.append("这轮还没拿到能直接引用的材料（可能是权限、授权或结果为空）。")
    if blockers:
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
    if budget_hit:
        analysis_bits.append("时间/步数预算到了；下面是已完成步骤，你可以点名让我继续补。")
    elif partial and blockers:
        analysis_bits.append("有几步没跑通，我按已拿到的材料先给你一版。")
    analysis = "\n".join(analysis_bits) if analysis_bits else "材料有限，关联还偏弱。"
    opinion = (
        "交叉项优先；单侧信号先放一放。"
        if (facts_pub and facts_live)
        else (
            "飞书侧已经有人和日程；周报侧若空，点人名我可以再查。"
            if facts_live and not facts_pub
            else "交叉还不够，暂不硬排序。"
        )
    )
    suggestion = (
        "补个人日历授权，或点一个群/人名继续收窄。"
        if graph.band == "complex"
        else "继续深挖直接丢人名或项目别名即可。"
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


def synthesize_work(
    user_text: str,
    envelopes: list[TieredEnvelope],
    *,
    identity: Any = None,
    session: Any = None,
    company_block: str = "",
    graph: TaskGraph | None = None,
    partial: bool = False,
    budget_hit: str = "",
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """默认：LLM 按用户完整原话 + 完整材料成文；失败才回落规则分栏。"""
    graph = graph or TaskGraph(goal=(user_text or "")[:200], mode="work")
    columns = format_columns(
        envelopes, graph=graph, partial=partial, budget_hit=budget_hit
    )
    meta: dict[str, Any] = {"llm_used": False, "model": None, "source": "rule_columns"}
    material = materials_block(envelopes)
    notes = []
    if budget_hit:
        notes.append(f"执行备注：预算触顶（{budget_hit}），材料可能不完整。")
    elif partial:
        notes.append("执行备注：部分步骤未成功，请据此如实说明缺口。")
    system = _SYNTH_SYSTEM
    if company_block:
        system += "\n\n" + company_block
    user = (
        f"用户完整原话（不得弱化）：\n{(user_text or '').strip()}\n\n"
        f"分桶材料：\n{material}\n"
    )
    if notes:
        user += "\n" + "\n".join(notes) + "\n"
    user += "\nMesh 完整答复："
    try:
        from ... import llm

        out = llm.call(system, user, max_tokens=4000, json_mode=False, task="answer")
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        meta["source"] = "llm_mouth"
        text = sanitize(str(out or "").strip())
        if not text:
            text = render_work_answer(columns)
            meta["source"] = "rule_columns_empty_llm"
        grounded = [
            e
            for e in envelopes
            if e.ok and (e.text or "").strip() and not (e.payload or {}).get("empty")
        ]
        if grounded and text and len((text or "").strip()) < 48 and max(len(e.text or "") for e in grounded) > 80:
            log.warning("supervisor mouth contradicted non-empty materials")
            text = render_work_answer(columns)
            meta["source"] = "rule_columns_contradiction"
        return text, meta, columns
    except Exception as e:
        log.warning("supervisor mouth synthesize failed: %s", e)
        meta["error"] = str(e)[:160]
        return render_work_answer(columns), meta, columns


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
