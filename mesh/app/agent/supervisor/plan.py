"""MeshSupervisor planner — one LLM owns tool choice and task split."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .types import DEFAULT_BUDGET, PlanStep, TaskGraph
from .workers import resolve_worker

log = logging.getLogger("uvicorn.error")

ALLOWED_READ_TOOLS = frozenset(
    {
        "ask.published",
        "ask.relations_summary",
        "context.list_issues",
        "crm.search",
        "feishu.search",
        "feishu.doc.get",
        "feishu.calendar.list",
        "feishu.calendar.propose",
        "feishu.discuss.summary",
    }
)

ALLOWED_WRITE_TOOLS = frozenset(
    {
        "feishu.doc.create",
        "feishu.calendar.create",
        "feishu.im.send",
    }
)

_PLANNER_SYSTEM = """你是 MeshSupervisor（全局掌控 Agent）。只规划与派工，不对用户说话，不做公司事实断言。

根据用户目标、公司先验、会话状态，输出一个 JSON：

{
  "mode": "speak|work|prepare_write|confirm_write|cancel_write|refuse",
  "band": "simple|ordinary|medium|complex",
  "goal": "一句话目标",
  "speak_hint": "仅 mode=speak 时可选提示",
  "refuse_text": "仅 mode=refuse",
  "write_tool": "仅 prepare_write：feishu.doc.create|feishu.calendar.create|feishu.im.send",
  "write_args": {},
  "steps": [
    {
      "id": "s1",
      "tool": "feishu.search|ask.published|...",
      "args": {},
      "depends_on": [],
      "parallel_group": "",
      "optional": false
    }
  ]
}

规则：
1) 需要查数/多源/关联 → mode=work，用 steps；任意用户措辞都按意图规划，禁止假设固定模板句
2) 闲聊/观点/写稿不落飞书 → mode=speak
3) 要写入飞书 → mode=prepare_write（系统会再请用户确认）；有待确认且用户同意 → confirm_write；取消 → cancel_write
4) steps 只用只读工具；写入绝不进 steps
5) feishu.search 必须带 resource_type（group|member|user|directory|doc|message|calendar|wiki|folder）
   - 列群：group（query 可空）
   - 群成员：member，且 depends_on 列群步骤；系统会注入 chat_id（不要空想 open_id）
   - 按姓名找人：directory + keyword=姓名
   - 列某队/部门有谁：directory + keyword=队名或部门名（如「品牌创意」「创意视频」）；系统会按飞书树 rollup，不要改去空转 ask.published
   - user 仅在已知 open_id 时使用
5b) 硅谷对外人脉/BD 跟进（思琪·Lilyann 的 Notion CRM）→ crm.search
   - 问「最近聊了谁/沟通/跟进」：mode=recent 或 query 含最近
   - 问某人「怎么样/下一步/判断」：mode=take 或默认 auto + 人名
   - 问公司档案：mode=company
   - 禁止把 CRM 结果说成已上线周报；CRM ≠ 飞书通讯录同事
5c) 「和某人/老板相关的事情」「最近跟某人有关」：必须并行 crm.search（query=解析后的全名）+ ask.published（同全名）；禁止只查飞书通讯录就说「没有」
5d) 用户反驳上文（「这个不是吗」「为什么说没有」「那你刚刚」）→ mode=work，按会话里的人名/主题重查 crm.search（必要时加 ask.published），禁止 mode=speak 空辩解
5e) 纯组织名单（「X团队/部门有谁」「我所在的部门」「详细到子部门」）→ 只用 feishu.search directory；禁止顺手加 ask.published
6) 周报事实用 ask.*；飞书 live 用 feishu.*；CRM 用 crm.search；禁止混成一个假事实源
7) 用户说「和我有关/我的周报」时：ask.published 的 query 必须写上对方姓名与团队（见下方身份），禁止让用户再报一遍部门
8) 若目标要「关联周报/这些人有关」：ask.published 必须 depends_on 列成员步骤，等拿到人名后再查周报（系统也会注入人名）
9) 用户可能用简称/工号（锦涛、思琪、49、老板）；系统会解析成全名。规划时用全名检索，不要要求用户必须打全名
10) 步骤 ≤8；有依赖才写 depends_on；可并行的标同一 parallel_group
11) 只输出 JSON
"""

_ORG_ROSTER_RE = re.compile(
    r"(有谁|都有谁|有哪些人|成员名单|子部门|我所在的部门|我的部门|哪个队|哪支队)"
)
_RELATED_AFFAIRS_RE = re.compile(r"(相关的事情|有关的事情|相关进展|近况|跟进|打交道|引荐)")
_CHALLENGE_RE = re.compile(
    r"(为什么说没有|这个不是吗|那你刚刚|你刚才|你不是说|明明有|漏了|刚才那)"
)
_PERSON_ARROW_RE = re.compile(r"([\u4e00-\u9fffA-Za-z·\.\s]{1,24})→([\u4e00-\u9fffA-Za-z·\.\s]{1,24})")
_CANON_IN_EXPAND_RE = re.compile(r"和([\u4e00-\u9fffA-Za-z]{2,12})（")


def _identity_line(identity: Any) -> str:
    if identity is None:
        return "对方身份：未知"
    person = getattr(identity, "person", None) or {}
    if not isinstance(person, dict):
        person = {}
    name = (
        str(getattr(identity, "display_hint", None) or "").strip()
        or str(person.get("display") or person.get("name") or "").strip()
    )
    team = str(getattr(identity, "primary_team", None) or "").strip()
    oid = str(getattr(identity, "feishu_open_id", None) or "").strip()
    status = str(getattr(identity, "status", None) or "").strip()
    bits = [f"绑定={status or 'unknown'}"]
    if name:
        bits.append(f"姓名={name}")
    if team:
        bits.append(f"团队={team}")
    if oid:
        bits.append(f"open_id={oid}")
    return "对方身份：" + "；".join(bits)


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
    except Exception:
        return {}
    return {}


def _normalize_steps(raw_steps: Any, *, goal: str) -> list[PlanStep]:
    if not isinstance(raw_steps, list):
        return []
    out: list[PlanStep] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_steps[: int(DEFAULT_BUDGET["plan_steps"])]):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool not in ALLOWED_READ_TOOLS:
            continue
        sid = str(item.get("id") or f"s{i+1}").strip() or f"s{i+1}"
        if sid in seen:
            sid = f"{sid}_{i+1}"
        seen.add(sid)
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        args = dict(args)
        if tool == "feishu.search":
            rt = str(args.get("resource_type") or item.get("resource_type") or "").strip().lower()
            if not rt:
                continue
            args["resource_type"] = rt
            args.setdefault("max_results", 12)
        if tool.startswith("ask.") and not str(args.get("query") or "").strip():
            args["query"] = goal[:160]
        deps = item.get("depends_on") if isinstance(item.get("depends_on"), list) else []
        deps = [str(d).strip() for d in deps if str(d).strip()]
        worker = str(item.get("worker") or "").strip() or resolve_worker(tool, args)
        out.append(
            PlanStep(
                id=sid,
                worker=worker,
                tool=tool,
                args=args,
                depends_on=deps,
                parallel_group=str(item.get("parallel_group") or "").strip(),
                optional=bool(item.get("optional")),
            )
        )
    ids = {s.id for s in out}
    for s in out:
        s.depends_on = [d for d in s.depends_on if d in ids and d != s.id]
    return out


def _steps_from_decide_shape(data: dict[str, Any], *, goal: str) -> list[PlanStep]:
    tool = str(data.get("tool") or "").strip()
    if tool not in ALLOWED_READ_TOOLS:
        return []
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    args = dict(args)
    q = str(data.get("query") or goal or "").strip()[:160]
    if tool.startswith("ask.") and not str(args.get("query") or "").strip():
        args["query"] = q or goal[:160]
    if tool == "feishu.search":
        rt = str(args.get("resource_type") or data.get("resource_type") or "").strip()
        if not rt:
            return []
        args["resource_type"] = rt
        args.setdefault("max_results", 12)
    return _normalize_steps([{"id": "s1", "tool": tool, "args": args}], goal=goal or q)


def _identity_bits(identity: Any) -> tuple[str, str]:
    if identity is None:
        return "", ""
    person = getattr(identity, "person", None) or {}
    if not isinstance(person, dict):
        person = {}
    name = (
        str(getattr(identity, "display_hint", None) or "").strip()
        or str(person.get("display") or person.get("name") or "").strip()
    )
    team = str(getattr(identity, "primary_team", None) or "").strip()
    return name, team


def _resolved_people_from_text(text: str, session: Any = None) -> list[str]:
    """从 expanded_query / 人名解析块 / session 抽全名。"""
    names: list[str] = []
    seen: set[str] = set()

    def _add(n: str) -> None:
        n = (n or "").strip(" ：:，,（）()")
        if not n or n in seen or len(n) < 2:
            return
        seen.add(n)
        names.append(n)

    for m in _PERSON_ARROW_RE.finditer(text or ""):
        _add(m.group(2))
    for m in _CANON_IN_EXPAND_RE.finditer(text or ""):
        _add(m.group(1))
    if session is not None:
        for n in getattr(session, "active_entities", None) or []:
            _add(str(n))
        for turn in reversed(list(getattr(session, "recent_turns", None) or [])[-6:]):
            if not isinstance(turn, dict):
                continue
            t = str(turn.get("text") or "")
            for m in _PERSON_ARROW_RE.finditer(t):
                _add(m.group(2))
            # 常见 CRM 人名残留
            for token in ("张鹏", "赵思琪", "思琪", "Sheng Z.", "Sheng Zha", "Gavin Ni"):
                if token in t:
                    _add(token.replace("思琪", "赵思琪") if token == "思琪" else token)
    return names[:8]


def _has_tool(steps: list[PlanStep], tool: str) -> bool:
    return any(s.tool == tool for s in steps)


def _ensure_step(
    steps: list[PlanStep],
    *,
    tool: str,
    args: dict[str, Any],
    sid: str,
    parallel_group: str = "p1",
) -> list[PlanStep]:
    for s in steps:
        if s.tool != tool:
            continue
        # 已有同工具：补强 query/mode/keyword
        merged = dict(s.args or {})
        for k, v in args.items():
            if v and not str(merged.get(k) or "").strip():
                merged[k] = v
            elif k in ("query", "keyword", "mode") and v:
                merged[k] = v
        s.args = merged
        return steps
    worker = resolve_worker(tool, args)
    steps.append(
        PlanStep(
            id=sid,
            worker=worker,
            tool=tool,
            args=dict(args),
            depends_on=[],
            parallel_group=parallel_group,
            optional=False,
        )
    )
    return steps


def _strip_ask_from_org_only(steps: list[PlanStep]) -> list[PlanStep]:
    kept = [s for s in steps if not s.tool.startswith("ask.")]
    return kept or steps


def apply_colleague_repairs(
    graph: TaskGraph,
    *,
    user_text: str,
    identity: Any = None,
    session: Any = None,
) -> tuple[TaskGraph, list[str]]:
    """确定性修补 Planner 常见漏召：老板相关、追问、纯组织名单。"""
    notes: list[str] = []
    q = (user_text or "").strip()
    steps = list(graph.steps or [])
    mode = graph.mode
    band = graph.band
    _, my_team = _identity_bits(identity)
    people = _resolved_people_from_text(q, session)

    # B) 反驳/追问上文
    if _CHALLENGE_RE.search(q):
        mode = "work"
        band = "medium" if band == "simple" else band
        focus = people[:]
        if not focus:
            focus = ["张鹏", "赵思琪"]
        crm_q = " ".join(focus[:3])
        steps = _ensure_step(
            steps,
            tool="crm.search",
            args={"query": crm_q, "mode": "auto"},
            sid="crm_challenge",
            parallel_group="p_fix",
        )
        steps = _ensure_step(
            steps,
            tool="ask.published",
            args={"query": crm_q},
            sid="ask_challenge",
            parallel_group="p_fix",
        )
        notes.append("challenge_reopen_crm")

    # A) 和某人/老板相关 → CRM + 周报
    if people and (
        _RELATED_AFFAIRS_RE.search(q)
        or ("相关" in q and ("事情" in q or "进展" in q or "最近" in q))
        or any(x in q for x in ("老板", "鹏总"))
    ):
        mode = "work"
        if band == "simple":
            band = "medium"
        canon = people[0]
        steps = _ensure_step(
            steps,
            tool="crm.search",
            args={"query": canon, "mode": "auto"},
            sid="crm_person",
            parallel_group="p_person",
        )
        steps = _ensure_step(
            steps,
            tool="ask.published",
            args={"query": f"{canon} 最近 相关"},
            sid="ask_person",
            parallel_group="p_person",
        )
        # 去掉「只查飞书通讯录」这种空转
        feishu_only_dir = [
            s
            for s in steps
            if s.tool == "feishu.search"
            and str((s.args or {}).get("resource_type") or "") == "directory"
            and not any(p in str((s.args or {}).get("keyword") or (s.args or {}).get("query") or "") for p in people)
        ]
        if feishu_only_dir and _has_tool(steps, "crm.search"):
            drop_ids = {s.id for s in feishu_only_dir}
            steps = [s for s in steps if s.id not in drop_ids]
        notes.append(f"person_related_crm:{canon}")

    # C) 纯组织名单：禁周报；我所在部门 → 注入团队；子部门追问
    org_followup = bool(re.search(r"子部门|再细|详细到", q))
    org_ask = bool(_ORG_ROSTER_RE.search(q) or re.search(r"(团队|部门).{0,6}(有谁|成员)", q))
    if org_ask or org_followup:
        mode = "work"
        band = "simple"
        kw = ""
        if re.search(r"我所在的?部门|我的部门|我们组|我们队", q):
            kw = my_team or "我所在部门"
        elif org_followup and session is not None:
            kw = str(getattr(session, "active_team", "") or "").strip()
            if not kw:
                last_q = str(getattr(session, "last_query", "") or "")
                for token in ("硅谷", "品牌创意", "商业化", "编辑部", "投资", "社群", "视频号", "播客"):
                    if token in last_q:
                        kw = token
                        break
        if not kw:
            # 从本句抽队名线索
            for token in (
                "硅谷",
                "品牌创意",
                "商业化",
                "编辑部",
                "投资",
                "社群",
                "视频号",
                "播客",
                "总裁办",
                "英文站",
            ):
                if token in q:
                    kw = token
                    break
        if not kw:
            kw = my_team or "组织"
        args = {
            "resource_type": "directory",
            "keyword": kw,
            "max_results": 50,
        }
        if org_followup or "子部门" in q:
            args["include_subdepartments"] = True
        steps = _ensure_step(
            steps,
            tool="feishu.search",
            args=args,
            sid="org_dir",
            parallel_group="p_org",
        )
        before = len(steps)
        steps = _strip_ask_from_org_only(steps)
        if len(steps) < before:
            notes.append("strip_ask_from_org")
        notes.append(f"org_directory:{kw}")

    # 思琪最近跟进类：确保 crm recent（若已有则保留）
    if re.search(r"(思琪|赵思琪|Lilyann).{0,8}(最近|跟进|沟通|聊了)", q) or re.search(
        r"(最近|近期).{0,6}(跟进|沟通|聊了谁)", q
    ):
        mode = "work"
        steps = _ensure_step(
            steps,
            tool="crm.search",
            args={"query": q[:80], "mode": "recent"},
            sid="crm_recent",
            parallel_group="p_crm",
        )
        notes.append("crm_recent_ensure")

    if notes:
        graph.mode = mode
        graph.band = band
        graph.steps = steps
        if mode == "work" and not steps:
            graph.mode = "speak"
    return graph, notes


def plan_turn(
    user_text: str,
    *,
    company_block: str = "",
    identity: Any = None,
    session: Any = None,
    observations: list[dict[str, Any]] | None = None,
) -> tuple[TaskGraph, dict[str, Any]]:
    """Single Supervisor plan (and optional replan with observations)."""
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": "", "source": "supervisor"}
    q = (user_text or "").strip()
    pending = getattr(session, "pending_write", None) if session is not None else None
    if isinstance(pending, dict) and pending.get("tool") and not observations:
        # 短确认由 loop 硬处理；这里仍允许模型在复合句里选 confirm/cancel/work
        pass

    system = _PLANNER_SYSTEM
    if company_block:
        system += "\n\n" + company_block
    user = f"{_identity_line(identity)}\n用户目标：{q}\n"
    if session is not None:
        last_q = str(getattr(session, "last_query", "") or "").strip()
        ents = [str(x) for x in (getattr(session, "active_entities", None) or []) if str(x).strip()]
        team = str(getattr(session, "active_team", "") or "").strip()
        if last_q or ents or team:
            user += "会话线索（追问时必须接着查，禁止假装没发生过）：\n"
            if last_q:
                user += f"- 上一问：{last_q[:160]}\n"
            if ents:
                user += "- 已解析人名：" + "、".join(ents[:8]) + "\n"
            if team:
                user += f"- 当前组织范围：{team}\n"
        turns = list(getattr(session, "recent_turns", None) or [])[-4:]
        if turns:
            bits = []
            for t in turns:
                if not isinstance(t, dict):
                    continue
                bits.append(f"{t.get('role')}: {str(t.get('text') or '')[:180]}")
            if bits:
                user += "最近对话摘录：\n" + "\n".join(bits) + "\n"
    if isinstance(pending, dict) and pending.get("tool"):
        user += (
            f"当前待确认写入：tool={pending.get('tool')} "
            f"args_keys={list((pending.get('args') or {}).keys())}\n"
        )
    if observations:
        user += "上一轮执行观察（请 replan 或改 mode）：\n"
        user += json.dumps(observations[:8], ensure_ascii=False)[:2500] + "\n"
        meta["replan"] = True
    user += "只输出任务 JSON："

    try:
        from ... import llm

        raw = llm.call(system, user, max_tokens=800, json_mode=True, task="answer")
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        data = _parse_json(raw)
    except Exception as e:
        log.warning("supervisor plan failed: %s", e)
        meta["error"] = str(e)[:160]
        meta["source"] = "llm_error"
        graph = TaskGraph(
            goal=q[:200],
            mode="work",
            band="ordinary",
            steps=_normalize_steps(
                [{"id": "s1", "tool": "ask.published", "args": {"query": q[:160]}}],
                goal=q,
            ),
        )
        graph, notes = apply_colleague_repairs(
            graph, user_text=q, identity=identity, session=session
        )
        meta["repairs"] = notes
        meta["step_ids"] = [s.id for s in graph.steps]
        meta["mode"] = graph.mode
        return graph, meta

    mode = str(data.get("mode") or data.get("action") or "speak").strip().lower()
    if mode == "ask":
        mode = "work"
    if mode not in (
        "speak",
        "work",
        "prepare_write",
        "confirm_write",
        "cancel_write",
        "refuse",
    ):
        mode = "speak"
    band = str(data.get("band") or "ordinary").strip().lower()
    if band not in ("simple", "ordinary", "medium", "complex"):
        band = "ordinary"
    goal = str(data.get("goal") or q)[:200]
    steps = _normalize_steps(data.get("steps"), goal=goal or q)
    if not steps and mode == "work":
        steps = _steps_from_decide_shape(data, goal=goal or q)
    write_tool = str(data.get("write_tool") or data.get("tool") or "").strip()
    write_args = data.get("write_args") if isinstance(data.get("write_args"), dict) else {}
    write_args = dict(write_args or {})
    if mode == "prepare_write" and write_tool not in ALLOWED_WRITE_TOOLS:
        # 误把读工具标成写 → 改 work
        if write_tool in ALLOWED_READ_TOOLS:
            mode = "work"
            if not steps:
                steps = _steps_from_decide_shape(
                    {"tool": write_tool, "query": data.get("query") or q, "args": write_args},
                    goal=goal or q,
                )
            write_tool = ""
        else:
            mode = "speak"
    graph = TaskGraph(
        goal=goal or q[:200],
        mode=mode,
        band=band,
        steps=steps,
        write_tool=write_tool if mode == "prepare_write" else "",
        write_args=write_args if mode == "prepare_write" else {},
        refuse_text=str(data.get("refuse_text") or data.get("text") or "")[:400],
        speak_hint=str(data.get("speak_hint") or "")[:200],
        budget=dict(DEFAULT_BUDGET),
    )
    graph, notes = apply_colleague_repairs(
        graph, user_text=q, identity=identity, session=session
    )
    meta["repairs"] = notes
    meta["step_ids"] = [s.id for s in graph.steps]
    meta["mode"] = graph.mode
    return graph, meta
