"""MeshSupervisor loop — assign, observe, verify, replan, one mouth."""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..session_state import SessionContextState
from .. import answer_stream as astream
from . import mouth
from . import plan as planmod
from . import progress as progressmod
from . import verify as verifymod
from . import workers
from . import write_gate
from .types import DEFAULT_BUDGET, PlanStep, SupervisorResult, TaskGraph, TieredEnvelope

log = logging.getLogger("uvicorn.error")


def supervisor_enabled() -> bool:
    v = (os.environ.get("MESH_SUPERVISOR") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _remember_org_scope(session: Any, envelopes: list[TieredEnvelope]) -> None:
    if session is None:
        return
    for e in envelopes or []:
        if str(e.tool) != "feishu.search":
            continue
        if str((e.payload or {}).get("resource_type") or "") != "directory":
            continue
        kw = str((e.payload or {}).get("keyword") or "").strip()
        if kw:
            session.active_team = kw[:80]
            return


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
    resolved_names: list[str] | None = None,
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
                    progressmod.emit_progress(label)

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
                    resolved_names=resolved_names,
                )
                calls += 1
                tools_called.append(step.tool)
                return env

            if len(group_steps) == 1:
                env = _run_one(group_steps[0])
                done[env.step_id] = env
            else:
                # I/O-bound 工具步骤可提高到 8 并行；由环境变量覆盖
                max_workers = int(os.environ.get("MESH_SUPERVISOR_STEP_WORKERS") or 8)
                with ThreadPoolExecutor(max_workers=min(max_workers, len(group_steps))) as pool:
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
    from ...request_cache import RequestCache

    # 请求级缓存：避免同请求内重复构建 company_context / person_resolve
    request_cache = RequestCache(ttl_s=60.0)

    q = (user_text or "").strip()
    company = company_context.assemble(
        identity=identity,
        permission=permission,
        context=context,
        session=session,
        user_text=q,
        request_cache=request_cache,
    )
    company_block = company.prompt_block()

    from .. import person_resolve as pr

    people_res = pr.resolve_people_in_text(q, session=session, request_cache=request_cache)
    pr.sanitize_session_people(session)
    pr.remember_hits(session, people_res.hits)
    resolved_names = [h.canonical for h in people_res.hits if getattr(h, "canonical", "")]
    if session is not None:
        session.last_query = q[:200]
        ident_team = str(getattr(identity, "primary_team", None) or "").strip()
        if ident_team and not str(getattr(session, "active_team", "") or "").strip():
            session.active_team = ident_team

    # Mouth：「我们团队」= Mesh 业务队；若用户「作为某队」则本轮用视角队，不串提问者主队
    try:
        from .plan import parse_acting_team

        acting = parse_acting_team(q)
        team = acting or str(getattr(identity, "primary_team", None) or "").strip()
        if team:
            from ..feishu_hands import org_directory as od

            teammates, label = od.our_team_members(team, limit=16)
            if acting:
                company_block += (
                    "\n\n## 本轮用户指定视角（角色扮演，≠提问者主队）\n"
                    f"按「{acting}」视角回答「该关注 / 我们」；"
                    "不要用提问者主队同事名单，也不要把硅谷 CRM 当默认关注名单"
                    "（除非用户明确问硅谷/人脉/BD）。\n"
                )
                if teammates:
                    company_block += (
                        f"{acting}同事参考：" + "、".join(teammates) + "\n"
                    )
            elif teammates:
                company_block += (
                    "\n\n## 提问者业务队同事（「我们团队」= Mesh 业务队："
                    + (label or team)
                    + "）\n"
                    + "、".join(teammates)
                    + "\n规则：这些人算提问者的团队；同业务队下各飞书叶子部门都算。"
                    + "被问到的人若材料里有「部门:」就直接用，不要说缺部门。"
                )
    except Exception:
        pass

    out = SupervisorResult(
        llm_used=False,
        trace={
            "supervisor": True,
            "colleague_v4": True,
            "person_resolve": people_res.to_dict(),
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

    timings: dict[str, int] = {}
    t_plan = time.monotonic()
    graph, pmeta = planmod.plan_turn(
        q,
        company_block=company_block,
        identity=identity,
        session=session,
        resolved_people=people_res.hits,
    )
    timings["plan_ms"] = int((time.monotonic() - t_plan) * 1000)
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
        t_mouth = time.monotonic()
        astream.begin_final_stream()
        try:
            text, smeta = mouth.speak(
                q,
                identity,
                session,
                company_block=company_block,
                hint=graph.speak_hint,
            )
        finally:
            astream.end_final_stream()
        timings["mouth_ms"] = int((time.monotonic() - t_mouth) * 1000)
        out.action = "speak"
        out.intent = "casual"
        out.text = text
        out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
        if smeta.get("model"):
            out.model = smeta.get("model")
        out.trace["mouth"] = "speak"
        out.trace["timings"] = timings
        return out

    t_execute = time.monotonic()
    # 主管路径补会话线索：会话状态 + 已解析人名，仅喂检索（不进成文原话）
    # 用户「作为某队」角色扮演时不要灌提问者主队 / 会话残留人名，否则周报召回被带跑。
    search_user_text = q
    try:
        from .plan import _ACTING_TEAM_RE, parse_acting_team

        acting = parse_acting_team(q)
        if acting:
            bare = _ACTING_TEAM_RE.sub("", q).strip(" ，,。.?？") or q
            search_user_text = f"{acting} {bare}"[:400]
        else:
            parts: list[str] = []
            team_ctx = str(getattr(session, "active_team", "") or "").strip()
            if team_ctx:
                parts.append(team_ctx)
            ents = list(getattr(session, "active_entities", None) or [])[:6]
            if ents:
                parts.append("、".join(str(x) for x in ents if str(x).strip()))
            prev = str(getattr(session, "last_query", "") or "").strip()
            if prev and prev != q:
                parts.append(prev[:120])
            extra = " ".join(p for p in parts if p).strip()
            if extra:
                search_user_text = f"{q} {extra}"[:400]
    except Exception:
        search_user_text = q
    envelopes, tools_called, budget_hit, progress = _execute_graph(
        graph,
        con=con,
        identity=identity,
        permission=permission,
        context=context,
        invoke_tool=invoke_tool,
        render_tool_result=render_tool_result,
        user_text=search_user_text,
        resolved_names=resolved_names,
    )
    timings["execute_ms"] = int((time.monotonic() - t_execute) * 1000)
    out.progress = list(progress)
    out.tools_called = list(tools_called)
    _remember_org_scope(session, envelopes)
    verdict = verifymod.verify(envelopes, graph=graph)
    out.trace["verify"] = {
        k: verdict[k]
        for k in ("ok_count", "total", "tiers", "cross_bucket", "want_replan", "replan_reason", "unused_sources")
        if k in verdict
    }

    t_replan = time.monotonic()
    replan_count = 0
    while verdict.get("want_replan") and replans_left > 0:
        replans_left -= 1
        replan_count += 1
        out.trace.setdefault("replans", []).append(verdict.get("replan_reason") or "replan")
        graph2, pmeta2 = planmod.plan_turn(
            q,
            company_block=company_block,
            identity=identity,
            session=session,
            observations=verdict.get("observations") or [],
            resolved_people=people_res.hits,
        )
        out.llm_used = bool(out.llm_used or pmeta2.get("llm_used"))
        out.trace.setdefault("replan_meta", []).append(pmeta2)
        if graph2.mode != "work" or not graph2.steps:
            break
        same_tools = [s.tool for s in graph2.steps] == [s.tool for s in graph.steps]
        same_args = [dict(s.args or {}) for s in graph2.steps] == [
            dict(s.args or {}) for s in graph.steps
        ]
        if same_tools and same_args:
            break
        graph = graph2
        t_reexec = time.monotonic()
        envelopes, tools_called2, budget_hit, progress2 = _execute_graph(
            graph,
            con=con,
            identity=identity,
            permission=permission,
            context=context,
            invoke_tool=invoke_tool,
            render_tool_result=render_tool_result,
            user_text=search_user_text,
            resolved_names=resolved_names,
        )
        timings[f"reexecute_{replan_count}_ms"] = int((time.monotonic() - t_reexec) * 1000)
        out.tools_called.extend(tools_called2)
        for p in progress2:
            if p not in out.progress:
                out.progress.append(p)
        _remember_org_scope(session, envelopes)
        verdict = verifymod.verify(envelopes, graph=graph)
        out.trace["verify"] = {
            k: verdict[k]
            for k in ("ok_count", "total", "tiers", "cross_bucket", "want_replan", "replan_reason", "unused_sources")
            if k in verdict
        }
    timings["replan_ms"] = int((time.monotonic() - t_replan) * 1000)
    timings["replan_count"] = replan_count

    skipped = len(graph.steps or []) - len(envelopes)
    partial = bool(budget_hit) or skipped > 0 or any(not e.ok for e in envelopes)
    t_mouth = time.monotonic()
    astream.begin_final_stream()
    try:
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
    finally:
        astream.end_final_stream()
    timings["mouth_ms"] = int((time.monotonic() - t_mouth) * 1000)
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
    out.trace["timings"] = timings
    log.info(
        "supervisor done mode=work band=%s tools=%s chars=%s progress=%s timings=%s",
        graph.band,
        out.tools_called,
        len(out.text or ""),
        out.progress,
        timings,
    )
    return out
