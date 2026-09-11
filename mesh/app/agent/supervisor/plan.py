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
   - 按姓名找人：directory + keyword（不要用 user 除非已有 open_id）
   - user 仅在已知 open_id 时使用
6) 周报事实用 ask.*；飞书 live 用 feishu.*；禁止混成一个假事实源
7) 用户说「和我有关/我的周报」时：ask.published 的 query 必须写上对方姓名与团队（见下方身份），禁止让用户再报一遍部门
8) 若目标要「关联周报/这些人有关」：ask.published 必须 depends_on 列成员步骤，等拿到人名后再查周报（系统也会注入人名）
9) 用户可能用简称/工号（锦涛、思琪、49）；系统会解析成全名。规划时用全名检索，不要要求用户必须打全名
10) 步骤 ≤8；有依赖才写 depends_on；可并行的标同一 parallel_group
11) 只输出 JSON
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
        # 兜底：单步 published，避免整轮变闲聊
        return (
            TaskGraph(
                goal=q[:200],
                mode="work",
                band="ordinary",
                steps=_normalize_steps(
                    [{"id": "s1", "tool": "ask.published", "args": {"query": q[:160]}}],
                    goal=q,
                ),
            ),
            meta,
        )

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
    meta["step_ids"] = [s.id for s in steps]
    meta["mode"] = mode
    return graph, meta
