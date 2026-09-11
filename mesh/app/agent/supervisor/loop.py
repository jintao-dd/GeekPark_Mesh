"""MeshSupervisor loop — assign, observe, verify, replan, one mouth."""
from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..session_state import SessionContextState
from . import mouth
from . import plan as planmod
from . import verify as verifymod
from . import workers
from . import write_gate
from .types import DEFAULT_BUDGET, PlanStep, SupervisorResult, TaskGraph, TieredEnvelope


def _published_looks_empty(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return bool(
        re.search(
            r"(没找到|没查到|没有.*记录|无可引用|没有可直接|未拿到|找不到)",
            t,
        )
    )


def _correlate_published_with_names(
    envelopes: list[TieredEnvelope],
    *,
    con: Any,
    identity: Any,
    permission: Any,
    context: Any,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
    user_text: str,
) -> tuple[list[TieredEnvelope], list[str]]:
    """飞书已拿到人名但周报空 → 用这些人名补一枪 ask.published。"""
    names = workers._names_from_prior(envelopes)
    if len(names) < 2:
        return envelopes, []
    pubs = [e for e in envelopes if str(e.tool).startswith("ask.")]
    live_ok = any(e.ok and e.tier == "feishu_live" for e in envelopes)
    if not live_ok:
        return envelopes, []
    if pubs and not any(_published_looks_empty(e.text) or not e.ok for e in pubs):
        return envelopes, []
    step = PlanStep(
        id="s_corr_pub",
        worker="published",
        tool="ask.published",
        args={"query": f"近期周报与 { '、'.join(names[:8]) } 相关的进展与触点"},
    )
    env = workers.run_step(
        step,
        con=con,
        identity=identity,
        permission=permission,
        context=context,
        invoke_tool=invoke_tool,
        render_tool_result=render_tool_result,
        prior=envelopes,
        user_text=user_text,
    )
    # 替换旧 published 或追加
    kept = [e for e in envelopes if not str(e.tool).startswith("ask.")]
    kept.append(env)
    return kept, [step.tool]

log = logging.getLogger("uvicorn.error")


def supervisor_enabled() -> bool:
    v = (os.environ.get("MESH_SUPERVISOR") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _execute_graph(
    graph: TaskGraph,
    *,
    con: Any,
    identity: Any,
    permission: Any,
    context: Any,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
    user_text: str = "",
) -> tuple[list[TieredEnvelope], list[str], str, list[str]]:
    t0 = time.monotonic()
    budget = dict(graph.budget or DEFAULT_BUDGET)
    max_steps = int(budget.get("plan_steps") or 8)
    max_calls = int(budget.get("tool_calls") or 12)
    wall = float(budget.get("wall_time_sec") or 60.0)
    steps = list(graph.steps or [])[:max_steps]
    done: dict[str, TieredEnvelope] = {}
    tools_called: list[str] = []
    progress: list[str] = []
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
            for s in group_steps:
                label = workers.progress_for(s.worker or workers.resolve_worker(s.tool, s.args))
                if label not in progress:
                    progress.append(label)

            def _run_one(step: PlanStep) -> TieredEnvelope:
                nonlocal calls
                prior = [done[d] for d in (step.depends_on or []) if d in done]
                # 同批之前已完成的步骤也可见（同图上下文）
                prior = list(done.values()) + prior
                env = workers.run_step(
                    step,
                    con=con,
                    identity=identity,
                    permission=permission,
                    context=context,
                    invoke_tool=invoke_tool,
                    render_tool_result=render_tool_result,
                    prior=prior,
                    user_text=user_text,
                )
                calls += 1
                tools_called.append(step.tool)
                return env

            if len(group_steps) == 1:
                env = _run_one(group_steps[0])
                done[env.step_id] = env
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(group_steps))) as pool:
                    futs = {pool.submit(_run_one, s): s for s in group_steps}
                    for fut in as_completed(futs):
                        env = fut.result()
                        done[env.step_id] = env
        if budget_hit:
            break

    ordered = [done[s.id] for s in steps if s.id in done]
    return ordered, tools_called, budget_hit, progress


def handle_turn(
    *,
    con: Any,
    user_text: str,
    identity: Any,
    permission: Any,
    context: Any,
    session: SessionContextState,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
) -> SupervisorResult:
    from .. import company_context

    q = (user_text or "").strip()
    company = company_context.assemble(
        identity=identity,
        permission=permission,
        context=context,
        session=session,
        user_text=q,
    )
    company_block = company.prompt_block()

    out = SupervisorResult(
        llm_used=False,
        trace={
            "supervisor": True,
            "colleague_v4": True,
            "company_understanding": {
                "ontology_team": company.ontology.primary_team,
                "wiki_matched": [p.slug for p in company.wiki.matched],
            },
        },
    )

    hard = write_gate.hard_confirm_or_cancel(q, session)
    if hard:
        graph = TaskGraph(goal=q[:200], mode=hard)
        wr = write_gate.run_write(
            mode=hard,
            graph=graph,
            con=con,
            user_text=q,
            identity=identity,
            permission=permission,
            context=context,
            session=session,
            invoke_tool=invoke_tool,
            render_tool_result=render_tool_result,
            company_block=company_block,
        )
        wr.trace = {**out.trace, **(wr.trace or {}), "hard_write": hard}
        return wr

    graph, pmeta = planmod.plan_turn(
        q,
        company_block=company_block,
        identity=identity,
        session=session,
    )
    out.llm_used = bool(pmeta.get("llm_used"))
    out.model = pmeta.get("model")
    out.trace["planner"] = pmeta
    out.trace["task_graph"] = graph.to_dict()
    replans_left = int((graph.budget or DEFAULT_BUDGET).get("replans") or 2)

    mode = graph.mode
    if mode in ("prepare_write", "confirm_write", "cancel_write"):
        wr = write_gate.run_write(
            mode=mode,
            graph=graph,
            con=con,
            user_text=q,
            identity=identity,
            permission=permission,
            context=context,
            session=session,
            invoke_tool=invoke_tool,
            render_tool_result=render_tool_result,
            company_block=company_block,
        )
        wr.llm_used = bool(wr.llm_used or out.llm_used)
        wr.trace = {**out.trace, **(wr.trace or {})}
        return wr

    if mode == "refuse":
        out.action = "refuse"
        out.refused = True
        out.deny_reason = "supervisor_refuse"
        out.intent = "refuse"
        out.text = mouth.sanitize(graph.refuse_text or "这个我做不了。")
        return out

    if mode == "speak" or not graph.steps:
        text, smeta = mouth.speak(
            q,
            identity,
            session,
            company_block=company_block,
            hint=graph.speak_hint,
        )
        out.action = "speak"
        out.intent = "casual"
        out.text = text
        out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
        if smeta.get("model"):
            out.model = smeta.get("model")
        out.trace["mouth"] = "speak"
        return out

    envelopes, tools_called, budget_hit, progress = _execute_graph(
        graph,
        con=con,
        identity=identity,
        permission=permission,
        context=context,
        invoke_tool=invoke_tool,
        render_tool_result=render_tool_result,
        user_text=q,
    )
    out.progress = list(progress)
    out.tools_called = list(tools_called)
    verdict = verifymod.verify(envelopes, graph=graph)
    out.trace["verify"] = {
        k: verdict[k]
        for k in ("ok_count", "total", "tiers", "cross_bucket", "want_replan", "replan_reason")
    }

    while verdict.get("want_replan") and replans_left > 0:
        replans_left -= 1
        out.trace.setdefault("replans", []).append(verdict.get("replan_reason") or "replan")
        graph2, pmeta2 = planmod.plan_turn(
            q,
            company_block=company_block,
            identity=identity,
            session=session,
            observations=verdict.get("observations") or [],
        )
        out.llm_used = bool(out.llm_used or pmeta2.get("llm_used"))
        out.trace.setdefault("replan_meta", []).append(pmeta2)
        if graph2.mode != "work" or not graph2.steps:
            break
        same = [s.tool for s in graph2.steps] == [s.tool for s in graph.steps]
        if same:
            break
        graph = graph2
        envelopes, tools_called2, budget_hit, progress2 = _execute_graph(
            graph,
            con=con,
            identity=identity,
            permission=permission,
            context=context,
            invoke_tool=invoke_tool,
            render_tool_result=render_tool_result,
            user_text=q,
        )
        out.tools_called.extend(tools_called2)
        for p in progress2:
            if p not in out.progress:
                out.progress.append(p)
        verdict = verifymod.verify(envelopes, graph=graph)
        out.trace["verify"] = {
            k: verdict[k]
            for k in ("ok_count", "total", "tiers", "cross_bucket", "want_replan", "replan_reason")
        }

    # 人名已在、周报空：补一轮关联 Ask（不依赖模型是否写对 depends_on）
    envelopes2, extra_tools = _correlate_published_with_names(
        envelopes,
        con=con,
        identity=identity,
        permission=permission,
        context=context,
        invoke_tool=invoke_tool,
        render_tool_result=render_tool_result,
        user_text=q,
    )
    if extra_tools:
        envelopes = envelopes2
        out.tools_called.extend(extra_tools)
        out.trace["correlate_published"] = True
        if "正在查已上线周报" not in out.progress:
            out.progress.append("正在查已上线周报")

    skipped = len(graph.steps or []) - len(envelopes)
    partial = bool(budget_hit) or skipped > 0 or any(not e.ok for e in envelopes)
    text, mouth_meta, columns = mouth.synthesize_work(
        q,
        envelopes,
        identity=identity,
        session=session,
        company_block=company_block,
        graph=graph,
        partial=partial,
        budget_hit=budget_hit,
    )
    out.llm_used = bool(out.llm_used or mouth_meta.get("llm_used"))
    if mouth_meta.get("model"):
        out.model = mouth_meta.get("model")
    out.synthesize_llm_used = bool(mouth_meta.get("llm_used"))
    out.trace["mouth"] = mouth_meta
    out.action = "ask"
    out.intent = (
        "feishu_search"
        if any(str(t).startswith("feishu.") for t in out.tools_called)
        else "ask_published"
    )
    out.text = mouth.sanitize(text)
    out.payload = {
        "columns": columns,
        "partial": partial,
        "complexity": graph.band,
        "source_tier": (
            "feishu_live"
            if any(str(t).startswith("feishu.") for t in out.tools_called)
            else "published"
        ),
        "orchestrated": True,
        "supervised": True,
        "progress": list(out.progress),
        "mouth_source": mouth_meta.get("source") or "",
    }
    out.trace["source_tier"] = out.payload["source_tier"]
    out.trace["progress"] = list(out.progress)
    out.trace["envelopes"] = [e.to_dict() for e in envelopes]
    out.trace["budget_hit"] = budget_hit
    log.info(
        "supervisor done mode=work band=%s tools=%s chars=%s progress=%s",
        graph.band,
        out.tools_called,
        len(out.text or ""),
        out.progress,
    )
    return out
