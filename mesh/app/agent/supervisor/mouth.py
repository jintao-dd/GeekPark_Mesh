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

# 用户问「最新 / 最近 / 新动态 / 最新消息」
_LATEST_QUERY_RE = re.compile(r"最新|最近|新动态|最新消息|最近进展|最近情况|最近动作", re.I)
# 成文里把证据里的有效事实否认掉的常见表达
_NEGATIVE_ANSWER_RE = re.compile(
    r"没有[最新|新|相关]|没有最新动态|没有消息|没有进展|没有.*动态|"
    r"无[最新|新|相关]|无最新动态|无消息|无进展|"
    r"未[有|见]|未有最新|未见.*动态|"
    r"(?:拿不出|给不出|凑不满|找不到).{0,8}(?:公司|人|创业者|条目|匹配)",
    re.I,
)

# 成文材料上限：够用即可；飞书卡片另有展示上限
_MATERIAL_CAP = 9000
_SNIPPET_CAP = 3500

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

任务：按用户原话目标，用分桶材料给一份**短而可执行**的答复。

硬规则：
1) 先结论/清单，再必要时补 1–3 条依据；不要散文，不要开场白。
2) 用户问了什么就答什么；材料不够就明说缺哪一块，不要假装答完，也不要扩写成周边话题。
3) published = 已上线周报；feishu_live = 飞书现场；crm_prior = 硅谷 CRM（思琪侧）。禁止把 CRM/飞书说成「周报里记录」。
4) 不要输出 JSON、action/tool、open_id/chat_id/budget 等协议词。
5) 名单/条目用短列表；同主题合并，不要逐条复读材料原文；但也不必刻意把每条都压成标题，关键事实要写出来。
6) 禁止「怎么串起来看 / 我的判断 / 建议下一步」这类套话分段；用户没问建议就不要给建议。
7) 禁止谎称已写入飞书。
8) 「我们团队 / 和我们相关」按材料里「提问者业务队同事」来认（Mesh 业务队，如品牌创意团队），
   同队下各飞书叶子部门都算；不要按叶子部门再切一刀。
   若材料写了「本轮用户指定视角」（如作为商业化），则本轮「我们 / 该关注」按该视角队答，
   不要用提问者主队同事顶替，也不要把硅谷 CRM 名单当成默认「该关注的人」。
   某人材料里已有「部门:」就据实说；周报「硅谷 BD 团队」只是内容标签。
   若名单里有海外拓展的赵思琪/Sean Shen/胡清远，且用户在问硅谷/海外相关，即使条目挂在「硅谷 BD」下也要纳入。
9) 问「相关 / 周报 / 进展」时以周报与人名进展为主；日历只作一句补充（除非用户明确问会议/忙不忙）。
10) 目标长度：普通问 ≤400 字；多问清单 ≤800 字。宁可短，不要注水。
11) **禁止谎称已查询某源**。若材料分桶里没有「硅谷 CRM（思琪侧）」，你绝不可写「CRM 没查出」「CRM 没带出内容」等句子；只说你实际查到的源即可。
12) 用户问「最新 / 最近 / 新动态 / 最新消息」时，必须按时间倒序列出近 1–2 期里的相关条目；禁止把「拟 / 待 / 将 / 观察 / 考虑 / 计划」等动作词过滤掉说成「没有最新动态」。只要证据里有带时间戳的相关事实，就要答出来。
13) 引用期次时使用 YYYY-MM-DD 格式（如 2026-08-17），保持统一；若材料中日期格式不统一，按此格式输出。
"""

_CROSS_SYNTH_EXTRA = """
11) 本轮材料含「CRM × 已上线周报 · 交叉」：按 mode=cross 成文，放宽长度与结构。
   - 目标长度 800–1200 字（材料够时写满关键节点，仍禁开场白与空话）。
   - 建议结构：①重叠结论与名单 → ②每个关键节点的 CRM 进展/我方接口 → ③需要对齐的点（周报侧出现但 CRM 侧口径不同处）。
   - CRM 桶与周报桶分栏引用；禁止把 CRM 写成「周报里记过」，也禁止只报「两边都有 N 个」而不写节点。
   - 规则 6 对本轮放宽：允许简短的「串起来看」一两段，但仍禁「建议下一步」套话（用户没问就不给）。
"""


def _envelopes_have_crm_cross(envelopes: list[TieredEnvelope]) -> bool:
    for e in envelopes or []:
        pl = getattr(e, "payload", None) or {}
        if not isinstance(pl, dict):
            continue
        if str(pl.get("mode") or "").strip().lower() == "cross":
            return True
        if pl.get("cross_op") or pl.get("cross_op_obs"):
            return True
        text = str(getattr(e, "text", None) or "")
        if "CRM × 已上线周报 · 交叉" in text or "硅谷 CRM × 已上线周报" in text:
            return True
    return False


def _mouth_max_tokens(*, cross: bool = False) -> int:
    import os

    try:
        base = max(400, min(2000, int(os.environ.get("MESH_MOUTH_MAX_TOKENS") or "1200")))
    except Exception:
        base = 1200
    if cross:
        return max(base, min(2400, int(os.environ.get("MESH_MOUTH_CROSS_MAX_TOKENS") or "1800")))
    return base


def _looks_like_latest_question(user_text: str) -> bool:
    return bool(_LATEST_QUERY_RE.search(user_text or ""))


def _looks_like_negative_answer(text: str) -> bool:
    return bool(_NEGATIVE_ANSWER_RE.search(text or ""))


def _has_grounded_evidence(envelopes: list[TieredEnvelope]) -> bool:
    for e in envelopes or []:
        if e.ok and (e.text or "").strip() and not (e.payload or {}).get("empty"):
            return True
    return False


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


def materials_block(envelopes: list[TieredEnvelope], *, planned_tools: list[str] | None = None) -> str:
    """拼完整材料给 LLM；只做展示清洗，不做语义弱化。

    同时显式标注本轮没有调用的源，防止成文编造「某源没查出」。
    """
    parts: list[str] = []
    blockers: list[str] = []
    seen_tiers: set[str] = set()
    for r in envelopes:
        if r.ok and (r.text or "").strip():
            seen_tiers.add(r.tier)
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

    # 标注未查询的源
    planned = [t.strip() for t in (planned_tools or []) if t.strip()]
    has_crm_step = any(t == "crm.search" for t in planned)
    has_ask_step = any(t in ("ask.published", "ask.relations_summary") for t in planned)
    has_feishu_step = any(t == "feishu.search" or t.startswith("feishu.") for t in planned)
    missing: list[str] = []
    if has_crm_step and "crm_prior" not in seen_tiers:
        missing.append("硅谷 CRM（思琪侧）：步骤计划了但无返回或返回为空")
    if has_ask_step and "published" not in seen_tiers:
        missing.append("已上线周报：步骤计划了但无返回或返回为空")
    if has_feishu_step and "feishu_live" not in seen_tiers:
        missing.append("飞书侧：步骤计划了但无返回或返回为空")
    if missing:
        parts.append("### 本轮未返回材料的源\n" + "\n".join(f"- {m}" for m in missing))

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


def _history_block(session: Any, *, limit: int = 6) -> str:
    """此前对话（供指代消解与话题延续）。只作上下文，不作事实。"""
    if session is None:
        return ""
    turns = list(getattr(session, "recent_turns", None) or [])[-limit:]
    lines: list[str] = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        role = "用户" if t.get("role") == "user" else "Mesh"
        text = str(t.get("text") or "").strip()
        if text:
            lines.append(f"{role}：{text[:220]}")
    if not lines:
        return ""
    return (
        "此前对话（仅用于理解指代和延续话题，不是事实来源；"
        "公司事实仍以本轮分桶材料为准）：\n" + "\n".join(lines)
    )


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
    planned_tools = [s.tool for s in (graph.steps or []) if s.tool]
    columns = format_columns(
        envelopes, graph=graph, partial=partial, budget_hit=budget_hit
    )
    meta: dict[str, Any] = {"llm_used": False, "model": None, "source": "rule_columns"}
    material = materials_block(envelopes, planned_tools=planned_tools)
    notes = []
    if budget_hit:
        notes.append(f"执行备注：预算触顶（{budget_hit}），材料可能不完整。")
    elif partial:
        notes.append("执行备注：部分步骤未成功，请据此如实说明缺口。")
    is_cross = _envelopes_have_crm_cross(envelopes)
    meta["crm_cross"] = is_cross
    system = _SYNTH_SYSTEM + (_CROSS_SYNTH_EXTRA if is_cross else "")
    if company_block:
        system += "\n\n" + company_block
    hist = _history_block(session)
    user = (
        (hist + "\n\n" if hist else "")
        + f"用户完整原话（不得弱化）：\n{(user_text or '').strip()}\n\n"
        f"分桶材料：\n{material}\n"
    )
    if notes:
        user += "\n" + "\n".join(notes) + "\n"
    if is_cross:
        user += "\nMesh 答复（交叉题：重叠结论 + 关键节点进展；分栏引用，别塌成空壳名单）："
    else:
        user += "\nMesh 答复（先结论，短列表，别注水）："
    try:
        from ... import llm

        out = llm.call(
            system,
            user,
            max_tokens=_mouth_max_tokens(cross=is_cross),
            json_mode=False,
            task="answer",
        )
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
        # cross 材料通常很长；短答更像塌缩，阈值略抬高
        min_chars = 96 if is_cross else 48
        if (
            grounded
            and text
            and len((text or "").strip()) < min_chars
            and max(len(e.text or "") for e in grounded) > 80
        ):
            log.warning("supervisor mouth contradicted non-empty materials")
            text = render_work_answer(columns)
            meta["source"] = "rule_columns_contradiction"

        # Guard：用户问「最新/最近」且答案写「没有」，但 evidence 非空 → 很可能是成文把有效事实降档了。
        # 用更严的系统 prompt 重抽一次；仍失败则回落规则分栏（规则分栏会把证据列出来）。
        if (
            _looks_like_latest_question(user_text)
            and _looks_like_negative_answer(text)
            and _has_grounded_evidence(envelopes)
            and not is_cross
        ):
            log.warning("supervisor mouth negative-latest guard triggered, retrying")
            retry_system = (
                system
                + "\n\n【硬性纠偏】用户明确问「最新/最近/新动态」。\n"
                "只要材料里存在带时间戳的相关事实，就必须列出，绝不能说「没有」。\n"
                "若确实只有旧材料，也按时间倒序列出最近 1–2 条，并说明时间。"
            )
            try:
                out2 = llm.call(
                    retry_system,
                    user,
                    max_tokens=_mouth_max_tokens(cross=is_cross),
                    json_mode=False,
                    task="answer",
                )
                text2 = sanitize(str(out2 or "").strip())
                if text2 and not _looks_like_negative_answer(text2):
                    text = text2
                    meta["source"] = "llm_mouth_retry_latest"
                else:
                    log.warning("retry still negative, falling back to rule columns")
                    text = render_work_answer(columns)
                    meta["source"] = "rule_columns_latest_guard"
            except Exception as e2:
                log.warning("latest guard retry failed: %s", e2)
                text = render_work_answer(columns)
                meta["source"] = "rule_columns_latest_guard_err"

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
