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

# 用户明确要日程/忙闲才放行日历工具；否则硬剥离（不靠 Prompt）
_CALENDAR_ASK_RE = re.compile(
    r"(日程|日历|会议|忙不忙|空闲|有空|约(?:个|一)?时间|几点开|排期|availability)",
    re.I,
)

# 用户明确要硅谷/对外人脉才放行 crm.search；否则硬剥离
# （会话里残留人名时 Planner 爱顺手加 CRM，把「商业化该关注」答成 CRM 名单）
_CRM_ASK_RE = re.compile(
    r"(硅谷|CRM|人脉库|对外人脉|创业者库|Notion\s*CRM|思琪侧|海外\s*BD|\bBD\b|湾区)",
    re.I,
)

# 「作为商业化的你 / 做为编辑部视角」→ 本轮回答视角（可与提问者主队不同）
_ACTING_TEAM_RE = re.compile(
    r"(?:作|做)为(?:一个|一名)?(?P<label>[\u4e00-\u9fffA-Za-z0-9 ·/]{2,24}?)"
    r"(?:团队|部)?"
    r"(?:的你|的角度|视角|身份|来看|来说|来讲|该关注)",
    re.I,
)


def user_asks_calendar(user_text: str) -> bool:
    return bool(_CALENDAR_ASK_RE.search(user_text or ""))


def user_asks_crm(user_text: str) -> bool:
    return bool(_CRM_ASK_RE.search(user_text or ""))


def parse_acting_team(user_text: str) -> str:
    """用户指定本轮回答视角队名；解析失败返回空串。不改 identity.primary_team。"""
    m = _ACTING_TEAM_RE.search(user_text or "")
    if not m:
        return ""
    label = str(m.group("label") or "").strip(" 的地得")
    if not label:
        return ""
    try:
        from ... import db

        for cand in (label, label + "团队", label.rstrip("团队") + "团队"):
            hit = db.normalize_team(cand)
            if hit:
                return hit
    except Exception:
        pass
    return ""


def _is_calendar_step(step: PlanStep) -> bool:
    tool = (step.tool or "").strip()
    if tool.startswith("feishu.calendar"):
        return True
    if tool == "feishu.search":
        rt = str((step.args or {}).get("resource_type") or "").strip().lower()
        return rt == "calendar"
    return False


def strip_calendar_unless_asked(steps: list[PlanStep], user_text: str) -> list[PlanStep]:
    """进展/周报类计划里丢掉日历步骤；用户明确问日程则保留。"""
    if not steps or user_asks_calendar(user_text):
        return list(steps or [])
    kept = [s for s in steps if not _is_calendar_step(s)]
    if len(kept) == len(steps):
        return kept
    ids = {s.id for s in kept}
    for s in kept:
        s.depends_on = [d for d in (s.depends_on or []) if d in ids and d != s.id]
    log.info(
        "planner stripped calendar steps (user did not ask schedule) kept=%s dropped=%s",
        len(kept),
        len(steps) - len(kept),
    )
    return kept


def strip_crm_unless_asked(steps: list[PlanStep], user_text: str) -> list[PlanStep]:
    """非硅谷/人脉问法里丢掉 crm.search；用户明确问才保留。"""
    if not steps or user_asks_crm(user_text):
        return list(steps or [])
    kept = [s for s in steps if (s.tool or "").strip() != "crm.search"]
    if len(kept) == len(steps):
        return kept
    ids = {s.id for s in kept}
    for s in kept:
        s.depends_on = [d for d in (s.depends_on or []) if d in ids and d != s.id]
    log.info(
        "planner stripped crm steps (user did not ask CRM/硅谷) kept=%s dropped=%s",
        len(kept),
        len(steps) - len(kept),
    )
    return kept


_PLANNER_SYSTEM = """你是 MeshSupervisor（全局掌控 Agent）。只规划与派工，不对用户说话，不做公司事实断言。

根据用户目标、工作记忆、公司先验，输出一个 JSON：

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

可用源（按意图选用，不要混成一个假事实源）：
- crm.search：硅谷对外人脉/BD 底库（People 枢纽、Interactions 事件、Takes 判断）。内部同事通讯录不是这个源。query 用解析后的全名；mode=auto|person|company|recent|take。
- ask.published：已上线周报事实。
- feishu.search：飞书现场。必须带 resource_type（group|member|user|directory|doc|message|calendar|wiki|folder）。
  directory 的 keyword 用队名/部门名/姓名，或提问者团队（工作记忆里有）；系统按飞书树展开子部门。列组织不要改去空转周报。
  member 必须 depends_on 列群步骤；user 仅在已知 open_id 时使用。
- feishu.calendar.* / feishu.doc.* / feishu.discuss.summary：日历、文档、讨论。

规划原则：
1) 需要查数、多源、关联、组织、对外人脉 → mode=work。按意图选源，禁止假设固定问法。
2) 工作记忆里已有具体同事（全名）时：即使问看法/建议，也必须 mode=work，先用这些全名走 ask.published（必要时 directory）；
   只有用户明确问硅谷/BD/对外人脉/CRM 时才加 crm.search。不要因为会话里残留人名就默认灌 CRM。
3) 组织归属（某组算不算某队、我们团队有谁）→ feishu.search directory；默认「我们团队」按提问者 Mesh 业务队；
   若用户说「作为/做为某队的你」则本轮按该视角队理解，不要用提问者主队同事名单顶替。周报桶名不是飞书上级。
4) 要写入飞书 → prepare_write（系统会再请用户确认）。
5) steps 只用只读工具；写入绝不进 steps。
6) 检索用工作记忆里的全名/团队，不要要求用户再报一遍。
7) 步骤 ≤8；有依赖才写 depends_on；可并行的标同一 parallel_group。
8) 问周报相关 / 个人或团队进展 /「该关注什么」→ 主步骤用 ask.published（可并行 directory）；
   不要用 feishu.calendar.* 当主步骤，除非用户明确问日程、会议、忙不忙、空闲。
9) 只输出 JSON。
"""


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


def work_memory_block(
    *,
    identity: Any = None,
    session: Any = None,
    resolved_people: list[Any] | None = None,
) -> str:
    """Planner 的一等输入：人、身份、上一轮，不是句式表。"""
    lines = ["## 工作记忆（规划用，不是事实）"]
    lines.append(_identity_line(identity))

    hits: list[str] = []
    for h in resolved_people or []:
        alias = str(getattr(h, "alias", "") or "").strip()
        canon = str(getattr(h, "canonical", "") or "").strip()
        if isinstance(h, dict):
            alias = str(h.get("alias") or "").strip()
            canon = str(h.get("canonical") or "").strip()
        if canon:
            bit = f"{alias}→{canon}" if alias and alias != canon else canon
            if bit not in hits:
                hits.append(bit)
    if session is not None:
        from .. import person_resolve as pr

        for n in pr.filter_known_people(getattr(session, "active_entities", None) or []):
            if n and n not in hits and not any(n in x for x in hits):
                hits.append(n)
    if hits:
        lines.append("已解析人名：" + "；".join(hits[:12]))
        lines.append("检索时用全名，不要只用称呼。")

    if session is None:
        return "\n".join(lines)

    last_q = str(getattr(session, "last_query", "") or "").strip()
    team = str(getattr(session, "active_team", "") or "").strip()
    if last_q:
        lines.append(f"上一问：{last_q[:200]}")
    if team:
        lines.append(f"当前组织范围：{team}")
    turns = list(getattr(session, "recent_turns", None) or [])[-4:]
    if turns:
        lines.append("最近对话：")
        for t in turns:
            if not isinstance(t, dict):
                continue
            role = str(t.get("role") or "")
            text = str(t.get("text") or "").strip()
            if text:
                lines.append(f"- {role}: {text[:220]}")
    return "\n".join(lines)


def _acting_team_memory_line(user_text: str, identity: Any = None) -> str:
    acting = parse_acting_team(user_text)
    if not acting:
        return ""
    asker = str(getattr(identity, "primary_team", None) or "").strip()
    if asker and asker != acting:
        return (
            f"本轮用户指定回答视角：{acting}"
            f"（提问者主队是 {asker}，不要用主队同事/「我们团队」顶替此视角；"
            "除非用户明确问硅谷/人脉，不要加 crm.search）。"
        )
    return f"本轮用户指定回答视角：{acting}（按该队周报材料答；勿默认灌 CRM）。"


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


def plan_turn(
    user_text: str,
    *,
    company_block: str = "",
    identity: Any = None,
    session: Any = None,
    observations: list[dict[str, Any]] | None = None,
    resolved_people: list[Any] | None = None,
) -> tuple[TaskGraph, dict[str, Any]]:
    """Single Supervisor plan (and optional replan with observations)."""
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": "", "source": "supervisor"}
    q = (user_text or "").strip()
    pending = getattr(session, "pending_write", None) if session is not None else None

    system = _PLANNER_SYSTEM
    if company_block:
        system += "\n\n" + company_block
    user = work_memory_block(
        identity=identity,
        session=session,
        resolved_people=resolved_people,
    )
    acting_line = _acting_team_memory_line(q, identity)
    if acting_line:
        user += "\n" + acting_line + "\n"
        meta["acting_team"] = parse_acting_team(q)
    user += f"\n用户目标：{q}\n"
    if isinstance(pending, dict) and pending.get("tool"):
        user += (
            f"当前待确认写入：tool={pending.get('tool')} "
            f"args_keys={list((pending.get('args') or {}).keys())}\n"
        )
    if observations:
        user += "上一轮执行观察（请换源或改 query 再 plan，不要重复同一空转）：\n"
        user += json.dumps(observations[:10], ensure_ascii=False)[:2800] + "\n"
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
    steps = strip_calendar_unless_asked(steps, q)
    steps = strip_crm_unless_asked(steps, q)
    if not steps and mode == "work":
        steps = _normalize_steps(
            [{"id": "s1", "tool": "ask.published", "args": {"query": q[:160]}}],
            goal=q,
        )
    write_tool = str(data.get("write_tool") or data.get("tool") or "").strip()
    write_args = data.get("write_args") if isinstance(data.get("write_args"), dict) else {}
    write_args = dict(write_args or {})
    if mode == "prepare_write" and write_tool not in ALLOWED_WRITE_TOOLS:
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
    meta["step_ids"] = [s.id for s in graph.steps]
    meta["mode"] = graph.mode
    return graph, meta
