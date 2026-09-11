"""Task Orchestrator — Complexity Judge + TaskPlan + Bounded ReAct + Specialists + Synthesis.

Architecture 固定；Execution Complexity 按用户目标分档（非 Stage）。
"""
from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

log = logging.getLogger("uvicorn.error")

# 预算初值；Capacity 实测后校准
DEFAULT_BUDGET = {
    "plan_steps": 8,
    "tool_calls": 12,
    "wall_time_sec": 60.0,
    "replans": 2,
}


def orchestrator_enabled() -> bool:
    v = (os.environ.get("MESH_COLLEAGUE_ORCHESTRATOR") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


@dataclass
class ComplexityJudgment:
    band: str  # simple|ordinary|medium|complex
    reason: str = ""
    signals: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def judge_complexity(user_text: str, *, session: Any = None) -> ComplexityJudgment:
    """运行时分档：描述目标难度，不是发版阶段。"""
    q = (user_text or "").strip()
    signals = {
        "group": bool(re.search(r"群聊|群成员|能访问的群|我的群|列出.*群", q)),
        "member": bool(re.search(r"成员|人员详情|群里的人", q)),
        "calendar": bool(re.search(r"日历|日程|忙闲|空档", q)),
        "weekly": bool(re.search(r"周报", q)),
        "correlate": bool(re.search(r"关联|梳理|值得关注|汇总|综合", q)),
        "dept": bool(re.search(r"部门|组织|哪队|团队", q)),
        "chat_who": bool(re.search(r"跟谁聊|聊过|最近联系", q)),
        "who_is": bool(re.search(r"是谁|哪位|什么人", q)),
        "write_doc": bool(re.search(r"写一篇|写篇|创建文档|润色", q)),
        "write_cal": bool(re.search(r"创建.*日程|建个?日历|帮我约", q)),
        "opinion": bool(re.search(r"值得关注|怎么看|你觉得", q)),
    }
    # pending write → 不走复杂编排抢确认
    pending = getattr(session, "pending_write", None) if session is not None else None
    if isinstance(pending, dict) and pending.get("tool"):
        return ComplexityJudgment(
            band="ordinary",
            reason="pending_write",
            signals=signals,
        )

    work_bits = sum(
        1
        for k in ("group", "member", "calendar", "weekly", "dept")
        if signals.get(k)
    )
    if work_bits >= 3 or (
        signals["group"] and signals["member"] and (signals["calendar"] or signals["weekly"])
    ):
        return ComplexityJudgment(
            band="complex",
            reason="multi_source_canary",
            signals=signals,
        )
    if signals["chat_who"] and (signals["opinion"] or signals["correlate"]):
        return ComplexityJudgment(band="medium", reason="chat_who_plus_judgment", signals=signals)
    if signals["chat_who"] or (signals["weekly"] and not signals["group"]):
        return ComplexityJudgment(band="ordinary", reason="single_enterprise_read", signals=signals)
    if signals["who_is"] or signals["dept"]:
        return ComplexityJudgment(band="simple", reason="identity_or_org", signals=signals)
    if signals["write_doc"] or signals["write_cal"]:
        return ComplexityJudgment(band="ordinary", reason="write_or_compose", signals=signals)
    return ComplexityJudgment(band="simple", reason="default_chat", signals=signals)


@dataclass
class PlanStep:
    id: str
    specialist: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str = ""
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskPlan:
    goal: str
    band: str
    steps: list[PlanStep] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_BUDGET))

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "band": self.band,
            "steps": [s.to_dict() for s in self.steps],
            "budget": dict(self.budget),
        }


ALLOWED_PLAN_TOOLS = frozenset(
    {
        "ask.published",
        "ask.relations_summary",
        "context.list_issues",
        "feishu.search",
        "feishu.doc.get",
        "feishu.calendar.list",
        "feishu.calendar.propose",
        "feishu.discuss.summary",
    }
)

_SPECIALIST_FOR_TOOL = {
    "ask.published": "published",
    "ask.relations_summary": "published",
    "context.list_issues": "published",
    "feishu.search": "research",
    "feishu.doc.get": "research",
    "feishu.discuss.summary": "research",
    "feishu.calendar.list": "calendar",
    "feishu.calendar.propose": "calendar",
}

_PLANNER_SYSTEM = """你是 Mesh 的 Task Planner（只规划，不回答用户，不做公司事实断言）。
根据用户目标与公司先验，输出一个 JSON 任务图。系统会按预算执行。

只输出 JSON：
{
  "band": "simple|ordinary|medium|complex",
  "goal": "一句话目标",
  "steps": [
    {
      "id": "s1",
      "specialist": "org|research|calendar|published",
      "tool": "工具名",
      "args": {},
      "depends_on": [],
      "parallel_group": "",
      "optional": false
    }
  ]
}

可用只读工具（写工具禁止出现在 steps）：
- ask.published — 已上线周报企业事实；args.query 必填，短而具体
- ask.relations_summary — 关系摘要；args.query
- context.list_issues — 已上线期次列表
- feishu.search — 飞书检索；args 必须含 resource_type=
  doc|message|group|wiki|folder|calendar|member|user|directory；
  列群用 group+query空；群成员用 member；通讯录用 directory+keyword；
  聊天用 message+短 keyword；文档用 doc
- feishu.doc.get — 取文档；args 需 token/url
- feishu.calendar.list — 日程/忙闲列表
- feishu.calendar.propose — 受控约时间（成员+空档）
- feishu.discuss.summary — 讨论摘要

规则：
1) 闲聊/无需工具 → steps=[]，band=simple
2) 单源够用 → 1 步；多源才多步；能并行的用同一 parallel_group
3) 有依赖才写 depends_on（用 id）
4) 步骤 ≤8；不要发明工具名；不要写飞书写入工具
5) 周报事实只用 ask.*；飞书 live 用 feishu.*；禁止混成一个假事实源
6) 按用户原意规划，不要假设他问了固定模板句
"""


def _parse_plan_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        import json

        return json.loads(text)
    except Exception:
        pass
    try:
        import json
        import re as _re

        m = _re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
    except Exception:
        return {}
    return {}


def _normalize_plan_steps(raw_steps: Any, *, goal: str) -> list[PlanStep]:
    if not isinstance(raw_steps, list):
        return []
    out: list[PlanStep] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_steps[: int(DEFAULT_BUDGET["plan_steps"])]):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool not in ALLOWED_PLAN_TOOLS:
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
        specialist = str(item.get("specialist") or "").strip() or _SPECIALIST_FOR_TOOL.get(
            tool, "research"
        )
        if tool == "feishu.search" and str(args.get("resource_type") or "") in (
            "group",
            "member",
            "user",
            "directory",
        ):
            specialist = "org"
        out.append(
            PlanStep(
                id=sid,
                specialist=specialist,
                tool=tool,
                args=args,
                depends_on=deps,
                parallel_group=str(item.get("parallel_group") or "").strip(),
                optional=bool(item.get("optional")),
            )
        )
    # 丢掉指向不存在 id 的依赖
    ids = {s.id for s in out}
    for s in out:
        s.depends_on = [d for d in s.depends_on if d in ids and d != s.id]
    return out


def plan_with_llm(
    user_text: str,
    *,
    company_block: str = "",
    identity: Any = None,
    session: Any = None,
    decision_hint: dict[str, Any] | None = None,
) -> tuple[TaskPlan, dict[str, Any]]:
    """LLM Planner：模型选工具与依赖；失败时退回空 plan（由上游决定是否单工具兜底）。"""
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": "", "source": "llm"}
    q = (user_text or "").strip()
    pending = getattr(session, "pending_write", None) if session is not None else None
    if isinstance(pending, dict) and pending.get("tool"):
        meta["source"] = "pending_write_skip"
        return TaskPlan(goal=q[:200], band="ordinary", steps=[]), meta

    hint = ""
    if isinstance(decision_hint, dict) and decision_hint:
        hint = (
            f"Decide 提示（可忽略）：action={decision_hint.get('action')} "
            f"tool={decision_hint.get('tool')} query={str(decision_hint.get('query') or '')[:80]}"
        )
    user = f"用户目标：{q}\n"
    if hint:
        user += hint + "\n"
    user += "只输出任务 JSON："
    system = _PLANNER_SYSTEM
    if company_block:
        system += "\n\n" + company_block
    try:
        from .. import llm

        raw = llm.call(
            system,
            user,
            max_tokens=700,
            json_mode=True,
            task="answer",
        )
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        data = _parse_plan_json(raw)
        band = str(data.get("band") or "ordinary").strip().lower()
        if band not in ("simple", "ordinary", "medium", "complex"):
            band = "ordinary"
        goal = str(data.get("goal") or q)[:200]
        steps = _normalize_plan_steps(data.get("steps"), goal=goal or q)
        # 模型误输出 Decide 形态（action/tool/query）→ 收成单步
        if not steps:
            hinted = plan_steps_from_decision(data, goal=goal or q)
            if hinted:
                steps = hinted
                meta["coerced_from_decide_shape"] = True
        # 无步骤但 band 很复杂 → 再 salvage 一次更短提示
        if not steps and band in ("medium", "complex"):
            raw2 = llm.call(
                system,
                user + "\n上次 steps 为空或非法。若目标需要查数，至少给出 1～4 个合法只读 steps。",
                max_tokens=700,
                json_mode=True,
                task="answer",
            )
            meta["salvage"] = True
            data2 = _parse_plan_json(raw2)
            steps = _normalize_plan_steps(data2.get("steps"), goal=goal or q)
            if not steps:
                steps = plan_steps_from_decision(data2, goal=goal or q)
            if str(data2.get("band") or "").strip().lower() in (
                "simple",
                "ordinary",
                "medium",
                "complex",
            ):
                band = str(data2.get("band")).strip().lower()
        # Decide 已点名合法只读工具 → 作单步兜底（非关键词穷举）
        if not steps and isinstance(decision_hint, dict):
            steps = plan_steps_from_decision(decision_hint, goal=goal or q)
            if steps:
                meta["source"] = "decision_hint"
                band = "ordinary"
        plan = TaskPlan(goal=goal or q[:200], band=band, steps=steps, budget=dict(DEFAULT_BUDGET))
        meta["step_ids"] = [s.id for s in steps]
        return plan, meta
    except Exception as e:
        log.warning("llm planner failed: %s", e)
        meta["error"] = str(e)[:160]
        meta["source"] = "llm_error"
        # 规划炸了仍尽量用 Decide 提示单步，避免整轮变闲聊
        steps: list[PlanStep] = []
        if isinstance(decision_hint, dict):
            steps = plan_steps_from_decision(decision_hint, goal=q)
            if steps:
                meta["source"] = "decision_hint_after_error"
        return TaskPlan(goal=q[:200], band="ordinary", steps=steps, budget=dict(DEFAULT_BUDGET)), meta


def plan_steps_from_decision(decision: dict[str, Any], *, goal: str) -> list[PlanStep]:
    """把 Decide 的 tool/query 收成 Planner 单步；非法工具返回空。"""
    if not isinstance(decision, dict):
        return []
    tool = str(decision.get("tool") or "").strip()
    if tool not in ALLOWED_PLAN_TOOLS:
        return []
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    args = dict(args)
    q = str(decision.get("query") or goal or "").strip()[:160]
    if tool.startswith("ask.") and not str(args.get("query") or "").strip():
        args["query"] = q or goal[:160]
    if tool == "feishu.search":
        rt = str(args.get("resource_type") or decision.get("resource_type") or "").strip()
        if not rt:
            return []
        args["resource_type"] = rt
        args.setdefault("max_results", 12)
    return _normalize_plan_steps(
        [{"id": "s1", "tool": tool, "args": args}],
        goal=goal or q,
    )


def build_plan(user_text: str, judgment: ComplexityJudgment) -> TaskPlan:
    """兼容旧调用：仅作极端兜底模板，主路径请用 plan_with_llm。"""
    q = (user_text or "").strip()
    band = judgment.band
    # 最小兜底：少步骤，避免正则多源模板假装「懂了」
    if band in ("complex", "medium"):
        return TaskPlan(
            goal=q[:200],
            band=band,
            steps=[
                PlanStep(
                    id="s1_groups",
                    specialist="org",
                    tool="feishu.search",
                    args={"resource_type": "group", "query": "", "max_results": 20},
                ),
                PlanStep(
                    id="s2_published",
                    specialist="published",
                    tool="ask.published",
                    args={"query": _published_query(q)},
                    parallel_group="fanout",
                    optional=True,
                ),
            ],
            budget=dict(DEFAULT_BUDGET),
        )
    if judgment.signals.get("weekly"):
        return TaskPlan(
            goal=q[:200],
            band=band,
            steps=[
                PlanStep(
                    id="s1_ask",
                    specialist="published",
                    tool="ask.published",
                    args={"query": q[:160]},
                )
            ],
            budget=dict(DEFAULT_BUDGET),
        )
    if judgment.signals.get("who_is") or judgment.signals.get("dept"):
        return TaskPlan(
            goal=q[:200],
            band=band,
            steps=[
                PlanStep(
                    id="s1_dir",
                    specialist="org",
                    tool="feishu.search",
                    args={
                        "resource_type": "directory",
                        "query": q[:40],
                        "args": {"keyword": _keyword_name(q)},
                        "max_results": 8,
                    },
                )
            ],
            budget=dict(DEFAULT_BUDGET),
        )
    return TaskPlan(goal=q[:200], band=band, steps=[], budget=dict(DEFAULT_BUDGET))


def _keyword_name(q: str) -> str:
    m = re.search(r"([\u4e00-\u9fff]{2,4})(?:是谁|哪队|哪个部门)", q)
    if m:
        return m.group(1)
    return q[:20]


def _published_query(q: str) -> str:
    # 复杂金丝雀：周报侧问关联，不要把整句飞书任务塞进 Ask
    if "周报" in q:
        return "近期周报里与团队协作、项目进展、人员相关的要点"
    return q[:120]


@dataclass
class StepResult:
    step_id: str
    specialist: str
    tool: str
    ok: bool
    source_tier: str
    text: str = ""
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrchestratorResult:
    band: str
    plan: TaskPlan
    step_results: list[StepResult] = field(default_factory=list)
    synthesis_text: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    partial: bool = False
    budget_hit: str = ""
    tools_called: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "plan": self.plan.to_dict(),
            "step_results": [s.to_dict() for s in self.step_results],
            "synthesis_text": self.synthesis_text,
            "columns": dict(self.columns),
            "partial": self.partial,
            "budget_hit": self.budget_hit,
            "tools_called": list(self.tools_called),
            "trace": dict(self.trace),
        }


def _tool_args(step: PlanStep) -> dict[str, Any]:
    args = dict(step.args or {})
    nested = args.pop("args", None)
    if isinstance(nested, dict):
        args.update(nested)
    if step.tool.startswith("ask.") or step.tool.startswith("context."):
        if "query" in args:
            return {"query": args.get("query") or ""}
    return args


def _render_intent_for(tool: str) -> str:
    t = (tool or "").strip()
    return {
        "feishu.search": "feishu_search",
        "feishu.doc.get": "feishu_doc_get",
        "feishu.calendar.list": "feishu_calendar_list",
        "feishu.calendar.propose": "feishu_calendar_list",
        "feishu.discuss.summary": "feishu_discuss",
        "ask.published": "ask_published",
        "ask.relations_summary": "ask_relations",
        "context.list_issues": "list_issues",
    }.get(t, "ask_published")


def execute_plan(
    plan: TaskPlan,
    *,
    con: Any,
    identity: Any,
    permission: Any,
    context: Any,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
) -> OrchestratorResult:
    """Bounded 执行：依赖序 + 同 parallel_group 可并行；超预算 partial。"""
    t0 = time.monotonic()
    budget = dict(plan.budget or DEFAULT_BUDGET)
    max_steps = int(budget.get("plan_steps") or 8)
    max_calls = int(budget.get("tool_calls") or 12)
    wall = float(budget.get("wall_time_sec") or 60.0)

    steps = list(plan.steps or [])[:max_steps]
    done: dict[str, StepResult] = {}
    tools_called: list[str] = []
    budget_hit = ""
    calls = 0
    chat_id = str(getattr(context, "chat_id", None) or "").strip()
    log.info(
        "orchestrator start band=%s steps=%s wall=%s",
        plan.band,
        [s.id for s in steps],
        wall,
    )

    def _ready(s: PlanStep) -> bool:
        return all(d in done for d in (s.depends_on or []))

    while len(done) < len(steps):
        if time.monotonic() - t0 > wall:
            budget_hit = "wall_time"
            break
        if calls >= max_calls:
            budget_hit = "tool_calls"
            break
        batch = [s for s in steps if s.id not in done and _ready(s)]
        if not batch:
            budget_hit = budget_hit or "dependency_stuck"
            break
        groups: dict[str, list[PlanStep]] = {}
        for s in batch:
            g = s.parallel_group or f"_serial_{s.id}"
            groups.setdefault(g, []).append(s)

        for _g, group_steps in groups.items():
            if time.monotonic() - t0 > wall or calls >= max_calls:
                budget_hit = budget_hit or (
                    "wall_time" if time.monotonic() - t0 > wall else "tool_calls"
                )
                break

            def _run_one(step: PlanStep) -> StepResult:
                nonlocal calls
                try:
                    args = _tool_args(step)
                    if chat_id and step.tool == "feishu.search":
                        rt = str(args.get("resource_type") or "")
                        if rt in ("member", "group", "message"):
                            args.setdefault("chat_id", chat_id)
                    result = invoke_tool(
                        step.tool,
                        con,
                        identity,
                        permission,
                        context,
                        args,
                    )
                    calls += 1
                    tools_called.append(step.tool)
                    intent = _render_intent_for(step.tool)
                    fact_text, _bindings, _ev = render_tool_result(
                        result,
                        intent,
                        getattr(identity, "status", "") or "",
                    )
                    ok = bool(getattr(result, "ok", False))
                    tier = (
                        "feishu_live"
                        if str(step.tool).startswith("feishu.")
                        else "published"
                    )
                    payload = getattr(result, "payload", None)
                    if not isinstance(payload, dict):
                        payload = {}
                    if payload.get("source_tier"):
                        tier = str(payload.get("source_tier"))
                    err = str(getattr(result, "error", "") or "")
                    # 勿把原始 JSON 协议塞进成文
                    text = (fact_text or "").strip()
                    if text.startswith("{") and '"action"' in text[:40]:
                        text = str(payload.get("answer") or payload.get("snippet") or text)[:2000]
                    return StepResult(
                        step_id=step.id,
                        specialist=step.specialist,
                        tool=step.tool,
                        ok=ok,
                        source_tier=tier,
                        text=text[:4000],
                        error=err,
                        payload={
                            k: payload.get(k)
                            for k in ("empty", "user_auth_required", "meta", "source_tier")
                            if k in payload
                        },
                    )
                except Exception as e:
                    log.warning("orchestrator step %s failed: %s", step.id, e)
                    return StepResult(
                        step_id=step.id,
                        specialist=step.specialist,
                        tool=step.tool,
                        ok=False,
                        source_tier="feishu_live",
                        error=str(e)[:200],
                    )

            if len(group_steps) == 1:
                sr = _run_one(group_steps[0])
                done[sr.step_id] = sr
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(group_steps))) as pool:
                    futs = {pool.submit(_run_one, s): s for s in group_steps}
                    for fut in as_completed(futs):
                        sr = fut.result()
                        done[sr.step_id] = sr

        if budget_hit:
            break

    ordered = [done[s.id] for s in steps if s.id in done]
    skipped = [s.id for s in steps if s.id not in done]
    partial = bool(budget_hit or skipped)
    columns = synthesize_columns(
        ordered, plan=plan, partial=partial, budget_hit=budget_hit
    )
    text = format_columns(columns)
    log.info(
        "orchestrator done band=%s ok_steps=%s/%s chars=%s partial=%s",
        plan.band,
        sum(1 for s in ordered if s.ok),
        len(steps),
        len(text or ""),
        partial,
    )
    return OrchestratorResult(
        band=plan.band,
        plan=plan,
        step_results=ordered,
        synthesis_text=text,
        columns=columns,
        partial=partial,
        budget_hit=budget_hit,
        tools_called=tools_called,
        trace={
            "skipped_steps": skipped,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "calls": calls,
        },
    )


def synthesize_columns(
    results: list[StepResult],
    *,
    plan: TaskPlan,
    partial: bool = False,
    budget_hit: str = "",
) -> dict[str, str]:
    """FACT / ANALYSIS / OPINION / SUGGESTION 分栏（规则版 Synthesizer）。"""
    facts_pub: list[str] = []
    facts_live: list[str] = []
    facts_other: list[str] = []
    errors: list[str] = []
    for r in results:
        if not r.ok:
            if r.error:
                errors.append(f"{r.tool}: {r.error}")
            else:
                errors.append(f"{r.tool}: 未成功")
            continue
        snippet = (r.text or "").strip()
        if not snippet:
            snippet = f"{r.tool} 有返回但无可读摘要"
        line = f"[{r.source_tier}/{r.specialist}] {snippet[:800]}"
        if r.source_tier == "published":
            facts_pub.append(line)
        elif r.source_tier in ("feishu_live",):
            facts_live.append(line)
        else:
            facts_other.append(line)

    fact_parts: list[str] = []
    if facts_pub:
        fact_parts.append("【已上线周报】\n" + "\n".join(facts_pub[:4]))
    if facts_live:
        fact_parts.append("【飞书 live】\n" + "\n".join(facts_live[:4]))
    if facts_other:
        fact_parts.append("【其他】\n" + "\n".join(facts_other[:2]))
    if not fact_parts:
        fact_parts.append("（本轮未拿到可引用事实；可能权限/授权/空结果。）")
    if errors:
        fact_parts.append("【受阻】\n" + "\n".join(errors[:6]))

    fact = "\n\n".join(fact_parts)
    analysis_bits: list[str] = []
    if facts_pub and facts_live:
        analysis_bits.append(
            "周报侧与飞书侧都有材料：下面分开看，不把飞书讨论写成「周报里记录」。"
        )
    if plan.band == "complex":
        analysis_bits.append(
            "这是多源任务（群/人/日历/周报）。相关性需要你结合自己的当前工作判断；"
            "我只把查到的结构与材料对齐列出。"
        )
    if partial:
        analysis_bits.append(
            f"任务部分完成（budget={budget_hit or 'skipped'}），以下基于已完成步骤。"
        )
    analysis = "\n".join(analysis_bits) if analysis_bits else "材料有限，关联强度偏弱。"

    opinion = (
        "我觉得优先盯「既出现在飞书协作、又在周报里有进展」的交叉项；"
        "只有单侧信号的先放一放。"
        if (facts_pub and facts_live)
        else "材料还不够交叉，我暂时不硬给排序结论。"
    )
    suggestion = (
        "下一步：补个人日历授权（若需要看「我的日程」），"
        "或指定一个群/人名，我可以把关联再收窄一版。"
        if plan.band == "complex"
        else "若要我继续深挖，直接丢人名或项目别名即可。"
    )
    return {
        "FACT": fact,
        "ANALYSIS": analysis,
        "OPINION": opinion,
        "SUGGESTION": suggestion,
    }


def format_columns(columns: dict[str, str]) -> str:
    order = ("FACT", "ANALYSIS", "OPINION", "SUGGESTION")
    labels = {
        "FACT": "我查到的",
        "ANALYSIS": "怎么串起来看",
        "OPINION": "我的判断",
        "SUGGESTION": "建议下一步",
    }
    blocks: list[str] = ["按你的目标，我分几块说："]
    for k in order:
        v = (columns.get(k) or "").strip()
        if not v:
            continue
        blocks.append(f"**{labels.get(k, k)}**\n{v}")
    return "\n\n".join(blocks).strip()


def should_orchestrate(judgment: ComplexityJudgment, decision_action: str) -> bool:
    """是否进入 Planner/执行：由 Decide 的 work/ask 决定，不再靠正则 band。"""
    if not orchestrator_enabled():
        return False
    action = (decision_action or "").strip().lower()
    if action in ("prepare_write", "confirm_write", "cancel_write", "refuse", "speak"):
        return False
    if action in ("work", "ask"):
        return True
    return False
