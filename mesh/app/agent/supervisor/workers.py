"""Workers: execute tools only; never speak to the user."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from .types import PlanStep, TieredEnvelope

log = logging.getLogger("uvicorn.error")

WORKER_FOR_TOOL = {
    "ask.published": "published",
    "ask.relations_summary": "published",
    "context.list_issues": "published",
    "crm.search": "crm",
    "feishu.search": "research",
    "feishu.doc.get": "research",
    "feishu.discuss.summary": "research",
    "feishu.calendar.list": "calendar",
    "feishu.calendar.propose": "calendar",
    "feishu.doc.create": "writer",
    "feishu.calendar.create": "writer",
    "feishu.im.send": "writer",
}

ORG_RESOURCE_TYPES = frozenset({"group", "member", "user", "directory"})

PROGRESS_LABEL = {
    "org": "正在查组织/群成员",
    "crm": "正在查硅谷 CRM（思琪侧）",
    "research": "正在查飞书资料",
    "calendar": "正在查日历",
    "published": "正在查已上线周报",
    "writer": "正在准备飞书写入",
}


def resolve_worker(tool: str, args: dict[str, Any] | None = None) -> str:
    tool = (tool or "").strip()
    if tool == "feishu.search":
        rt = str((args or {}).get("resource_type") or "").strip().lower()
        if rt in ORG_RESOURCE_TYPES:
            return "org"
        return "research"
    return WORKER_FOR_TOOL.get(tool, "research")


def progress_for(worker: str) -> str:
    return PROGRESS_LABEL.get(worker, f"正在执行 {worker}")


def _identity_bits(identity: Any) -> tuple[str, str, str]:
    if identity is None:
        return "", "", ""
    person = getattr(identity, "person", None) or {}
    if not isinstance(person, dict):
        person = {}
    name = (
        str(getattr(identity, "display_hint", None) or "").strip()
        or str(person.get("display") or person.get("name") or "").strip()
    )
    team = str(getattr(identity, "primary_team", None) or "").strip()
    oid = str(getattr(identity, "feishu_open_id", None) or "").strip()
    return name, team, oid


def _chat_ids_from_prior(prior: list[TieredEnvelope]) -> list[str]:
    out: list[str] = []
    for e in prior or []:
        for cid in e.payload.get("chat_ids") or []:
            s = str(cid or "").strip()
            if s and s not in out:
                out.append(s)
        text = e.text or ""
        for m in re.finditer(r"\boc_[a-zA-Z0-9]+\b", text):
            s = m.group(0)
            if s not in out:
                out.append(s)
    return out


def _open_ids_from_prior(prior: list[TieredEnvelope]) -> list[str]:
    out: list[str] = []
    for e in prior or []:
        for oid in e.payload.get("open_ids") or []:
            s = str(oid or "").strip()
            if s.startswith("ou_") and s not in out:
                out.append(s)
        text = e.text or ""
        for m in re.finditer(r"\bou_[a-zA-Z0-9]+\b", text):
            s = m.group(0)
            if s not in out:
                out.append(s)
    return out


def _tool_args(step: PlanStep) -> dict[str, Any]:
    args = dict(step.args or {})
    nested = args.pop("args", None)
    if isinstance(nested, dict):
        args.update(nested)
    if step.tool.startswith("ask.") or step.tool.startswith("context."):
        if "query" in args:
            return {"query": args.get("query") or ""}
    return args


def enrich_args(
    step: PlanStep,
    *,
    identity: Any,
    context: Any,
    prior: list[TieredEnvelope] | None = None,
    user_text: str = "",
    resolved_names: list[str] | None = None,
) -> dict[str, Any]:
    """补全 chat_id / open_id / 检索线索；不做句式路由。"""
    args = _tool_args(step)
    name, team, self_oid = _identity_bits(identity)
    prior = list(prior or [])
    q_user = (user_text or "").strip()
    resolved = [str(n).strip() for n in (resolved_names or []) if str(n).strip()]

    if step.tool == "crm.search":
        q_plan = str(args.get("query") or "").strip()
        if not q_plan and resolved:
            q_plan = resolved[0]
        elif not q_plan:
            q_plan = q_user[:80]
        args["query"] = q_plan[:80]
        args.setdefault("mode", str(args.get("mode") or "auto"))
        return args

    if step.tool.startswith("ask."):
        q_plan = str(args.get("query") or args.get("q") or "").strip()
        q_full = q_user or q_plan
        names = list(resolved) + [n for n in _names_from_prior(prior) if n not in resolved]
        if name and name not in names:
            names = [name] + names
        teammates: list[str] = []
        if team:
            try:
                from ..feishu_hands import org_directory as od

                teammates = od.member_names_for_scope(team, limit=20)
            except Exception:
                teammates = []
            for t in teammates:
                if t and t not in names:
                    names.append(t)
        # 结构化扩召回；不要把成文指令塞进 FTS query
        args["query"] = q_full
        args["q"] = q_full
        if names:
            args["person_names"] = names[:20]
        # 显式桶过滤才设 team；默认不 apply_team_focus（飞书子树 ≠ 周报桶）
        if str(args.get("team") or args.get("team_filter") or "").strip():
            args.setdefault("apply_team_focus", False)
        log.info(
            "ask enrich team=%s person_names=%s q_len=%s",
            team or "-",
            len(names),
            len(q_full),
        )
        return args

    if step.tool != "feishu.search":
        return args

    rt = str(args.get("resource_type") or "").strip().lower()
    if rt == "directory":
        kw = str(args.get("keyword") or args.get("query") or "").strip()
        if not kw:
            kw = team or (resolved[0] if resolved else "")
        if kw:
            args["keyword"] = kw
        args.setdefault("include_subdepartments", True)
        args.setdefault("max_results", 50)
    ctx_chat = str(getattr(context, "chat_id", None) or "").strip()
    if ctx_chat and rt in ("member", "group", "message"):
        args.setdefault("chat_id", ctx_chat)

    if rt == "member" and not str(args.get("chat_id") or "").strip():
        cids = _chat_ids_from_prior(prior)
        if cids:
            args["chat_id"] = cids[0]

    if rt == "user":
        oids = [str(x).strip() for x in (args.get("open_ids") or []) if str(x).strip()]
        one = str(args.get("open_id") or args.get("user_id") or "").strip()
        if one:
            oids = [one] + [x for x in oids if x != one]
        if not oids:
            prior_oids = _open_ids_from_prior(prior)
            if self_oid and not str(args.get("query") or args.get("keyword") or "").strip():
                args["open_id"] = self_oid
            elif prior_oids:
                args["open_ids"] = prior_oids[:8]
            else:
                kw = str(args.get("keyword") or args.get("query") or name or "").strip()
                args["resource_type"] = "directory"
                args["keyword"] = kw or "同事"
                args.pop("open_id", None)
                args.pop("open_ids", None)
                log.info("worker rewrite user→directory keyword=%s", kw[:40])

    return args


def _render_intent(tool: str) -> str:
    return {
        "feishu.search": "feishu_search",
        "feishu.doc.get": "feishu_doc_get",
        "feishu.calendar.list": "feishu_calendar_list",
        "feishu.calendar.propose": "feishu_calendar_list",
        "feishu.discuss.summary": "feishu_discuss",
        "ask.published": "ask_published",
        "ask.relations_summary": "ask_relations",
        "crm.search": "crm_search",
        "context.list_issues": "list_issues",
        "feishu.doc.create": "feishu_write",
        "feishu.calendar.create": "feishu_write",
        "feishu.im.send": "feishu_write",
    }.get((tool or "").strip(), "ask_published")


def _extract_ids(payload: dict[str, Any]) -> dict[str, Any]:
    chat_ids: list[str] = []
    open_ids: list[str] = []
    person_names: list[str] = []
    group_titles: list[str] = []
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    for it in items[:40]:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "").strip()
        title = str(it.get("title") or "").strip()
        dtype = str(it.get("docs_type") or it.get("type") or "").lower()
        if iid.startswith("oc_") and iid not in chat_ids:
            chat_ids.append(iid)
        if iid.startswith("ou_") and iid not in open_ids:
            open_ids.append(iid)
        if "group" in dtype or "chat" in dtype:
            if iid.startswith("oc_") and iid not in chat_ids:
                chat_ids.append(iid)
            if title and title not in group_titles:
                group_titles.append(title)
        if dtype in ("member", "user", "person"):
            if iid.startswith("ou_") and iid not in open_ids:
                open_ids.append(iid)
            if title and not title.startswith("ou_") and title not in person_names:
                person_names.append(title)
        # 无 docs_type 时：标题像人名、snippet 像 open_id
        snip = str(it.get("snippet") or "").strip()
        if (
            title
            and snip.startswith("ou_")
            and title not in person_names
            and not title.startswith("ou_")
        ):
            person_names.append(title)
    return {
        "chat_ids": chat_ids,
        "open_ids": open_ids,
        "person_names": person_names,
        "group_titles": group_titles,
    }


def _names_from_prior(prior: list[TieredEnvelope]) -> list[str]:
    out: list[str] = []
    for e in prior or []:
        for n in e.payload.get("person_names") or []:
            s = str(n or "").strip()
            if s and s not in out and not s.startswith("ou_"):
                out.append(s)
        # 从成文里捞「- 张三」行
        for m in re.finditer(r"(?m)^[\-\*]\s*([^\s—\-]{2,20})", e.text or ""):
            name = m.group(1).strip()
            if (
                name
                and name not in out
                and not name.startswith("ou_")
                and not name.startswith("oc_")
                and "忙碌" not in name
                and "群" not in name
            ):
                out.append(name)
    return out[:12]


def run_step(
    step: PlanStep,
    *,
    con: Any,
    identity: Any,
    permission: Any,
    context: Any,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
    prior: list[TieredEnvelope] | None = None,
    user_text: str = "",
    resolved_names: list[str] | None = None,
) -> TieredEnvelope:
    worker = step.worker or resolve_worker(step.tool, step.args)
    try:
        args = enrich_args(
            step,
            identity=identity,
            context=context,
            prior=prior,
            user_text=user_text,
            resolved_names=resolved_names,
        )
        worker = resolve_worker(step.tool, args)
        result = invoke_tool(step.tool, con, identity, permission, context, args)
        intent = _render_intent(step.tool)
        fact_text, _bindings, _ev = render_tool_result(
            result,
            intent,
            getattr(identity, "status", "") or "",
        )
        ok = bool(getattr(result, "ok", False))
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        tier = "feishu_live" if str(step.tool).startswith("feishu.") else "published"
        if payload.get("source_tier"):
            tier = str(payload.get("source_tier"))
        err = str(getattr(result, "error", "") or "")
        text = (fact_text or "").strip()
        if text.startswith("{") and '"action"' in text[:40]:
            text = str(payload.get("answer") or payload.get("snippet") or text)[:2000]
        ids = _extract_ids(payload)
        need_replan = False
        replan_reason = ""
        if not ok and err in (
            "user_auth_required",
            "empty",
            "not_found",
            "open_id_required_for_user",
            "chat_id_required_for_members",
        ):
            need_replan = True
            replan_reason = err
        elif payload.get("empty") or (ok and not text):
            need_replan = True
            replan_reason = "empty_result"
        return TieredEnvelope(
            step_id=step.id,
            worker=worker,
            tool=step.tool,
            ok=ok,
            tier=tier,
            text=text[:12000],
            error=err,
            need_replan=need_replan,
            replan_reason=replan_reason,
            payload={
                **{
                    k: payload.get(k)
                    for k in ("empty", "user_auth_required", "meta", "source_tier")
                    if k in payload
                },
                **ids,
                "resource_type": str(args.get("resource_type") or ""),
                "keyword": str(args.get("keyword") or args.get("query") or "")[:80],
            },
        )
    except Exception as e:
        log.warning("worker step %s failed: %s", step.id, e)
        return TieredEnvelope(
            step_id=step.id,
            worker=worker,
            tool=step.tool,
            ok=False,
            tier="system",
            error=str(e)[:200],
            need_replan=True,
            replan_reason="exception",
        )
