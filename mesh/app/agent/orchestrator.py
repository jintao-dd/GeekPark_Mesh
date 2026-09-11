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


def build_plan(user_text: str, judgment: ComplexityJudgment) -> TaskPlan:
    q = (user_text or "").strip()
    band = judgment.band
    steps: list[PlanStep] = []
    if band == "complex":
        steps = [
            PlanStep(
                id="s1_groups",
                specialist="org",
                tool="feishu.search",
                args={"resource_type": "group", "query": "", "max_results": 20},
            ),
            PlanStep(
                id="s2_members",
                specialist="org",
                tool="feishu.search",
                args={"resource_type": "member", "query": "", "max_results": 50},
                depends_on=["s1_groups"],
            ),
            PlanStep(
                id="s3_calendar",
                specialist="calendar",
                tool="feishu.calendar.list",
                args={"query": q[:80], "max_results": 15},
                depends_on=["s2_members"],
                parallel_group="fanout",
                optional=True,
            ),
            PlanStep(
                id="s4_published",
                specialist="published",
                tool="ask.published",
                args={"query": _published_query(q)},
                depends_on=["s2_members"],
                parallel_group="fanout",
            ),
        ]
    elif band == "medium":
        steps = [
            PlanStep(
                id="s1_messages",
                specialist="research",
                tool="feishu.search",
                args={"resource_type": "message", "query": q[:80], "max_results": 12},
            ),
            PlanStep(
                id="s2_published",
                specialist="published",
                tool="ask.published",
                args={"query": q[:120]},
                parallel_group="fanout",
                optional=True,
            ),
        ]
    elif band == "ordinary":
        if judgment.signals.get("weekly") and not judgment.signals.get("chat_who"):
            steps = [
                PlanStep(
                    id="s1_ask",
                    specialist="published",
                    tool="ask.published",
                    args={"query": q[:160]},
                )
            ]
        else:
            steps = [
                PlanStep(
                    id="s1_search",
                    specialist="research",
                    tool="feishu.search",
                    args={
                        "resource_type": "message" if judgment.signals.get("chat_who") else "doc",
                        "query": q[:80],
                        "max_results": 10,
                    },
                )
            ]
    else:  # simple
        if judgment.signals.get("who_is") or judgment.signals.get("dept"):
            steps = [
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
            ]
        else:
            steps = []
    return TaskPlan(goal=q[:200], band=band, steps=steps, budget=dict(DEFAULT_BUDGET))


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
        # 同 parallel_group 并行，其余串行保序
        groups: dict[str, list[PlanStep]] = {}
        for s in batch:
            g = s.parallel_group or f"_serial_{s.id}"
            groups.setdefault(g, []).append(s)

        for _g, group_steps in groups.items():
            if time.monotonic() - t0 > wall or calls >= max_calls:
                budget_hit = budget_hit or ("wall_time" if time.monotonic() - t0 > wall else "tool_calls")
                break

            def _run_one(step: PlanStep) -> StepResult:
                nonlocal calls
                try:
                    result = invoke_tool(
                        step.tool,
                        con,
                        identity,
                        permission,
                        context,
                        _tool_args(step),
                    )
                    calls += 1
                    tools_called.append(step.tool)
                    fact_text, _bindings, _ev = render_tool_result(
                        result,
                        "feishu_search"
                        if str(step.tool).startswith("feishu.")
                        else "ask",
                        getattr(identity, "status", "") or "",
                    )
                    ok = bool(getattr(result, "ok", False))
                    tier = "feishu_live" if str(step.tool).startswith("feishu.") else "published"
                    payload = getattr(result, "payload", None)
                    if not isinstance(payload, dict):
                        payload = {}
                    # 从 envelope / payload 抽 tier
                    if payload.get("source_tier"):
                        tier = str(payload.get("source_tier"))
                    err = str(getattr(result, "error", "") or "")
                    return StepResult(
                        step_id=step.id,
                        specialist=step.specialist,
                        tool=step.tool,
                        ok=ok,
                        source_tier=tier,
                        text=(fact_text or "")[:4000],
                        error=err,
                        payload=payload,
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
    columns = synthesize_columns(ordered, plan=plan, partial=partial, budget_hit=budget_hit)
    text = format_columns(columns)
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
        "FACT": "事实",
        "ANALYSIS": "分析",
        "OPINION": "我的判断",
        "SUGGESTION": "建议",
    }
    blocks: list[str] = []
    for k in order:
        v = (columns.get(k) or "").strip()
        if not v:
            continue
        blocks.append(f"**{labels.get(k, k)}**\n{v}")
    return "\n\n".join(blocks).strip()


def should_orchestrate(judgment: ComplexityJudgment, decision_action: str) -> bool:
    if not orchestrator_enabled():
        return False
    action = (decision_action or "").strip().lower()
    if action in ("prepare_write", "confirm_write", "cancel_write", "refuse"):
        return False
    if judgment.band in ("medium", "complex"):
        return True
    if judgment.band == "ordinary" and judgment.reason in (
        "single_enterprise_read",
    ):
        # ordinary 仍可用编排跑单步，便于统一分栏；也可走旧 ask
        return True
    if judgment.band == "simple" and judgment.signals.get("who_is"):
        return True
    return False
