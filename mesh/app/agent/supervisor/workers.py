"""Workers: execute tools only; never speak to the user.

映射：
- org / research ← feishu.search（resource_type 分流）
- calendar ← feishu.calendar.*
- published ← ask.* / context.list_issues
- crm ← crm.search
- writer ← feishu.doc.create / calendar.create / im.send（仅 WriteGate confirm 走 run_step）
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from .types import PlanStep, TieredEnvelope

log = logging.getLogger("uvicorn.error")

# 与 adapters 同口径：这部分只喂检索，不进成文
_CLUE_MARK = "【检索线索"

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
    return args


# 「我们团队」才扩队友；「和我相关」只钉本人，避免把整棵子树灌进 person_names
_TEAM_SCOPE_RE = re.compile(
    r"(我们|咱们)(的)?(团队|部门|组|组里)|团队相关|组里相关|部门相关|本组|本团队"
)
_SELF_SCOPE_RE = re.compile(
    r"(和|与)?我(有关|相关|的周报|最近|这边|的进展)|关于我|我自己|本人"
)


def _ask_person_scope(q: str, *, has_resolved: bool) -> str:
    if _TEAM_SCOPE_RE.search(q or ""):
        return "team"
    if _SELF_SCOPE_RE.search(q or ""):
        return "self"
    if has_resolved:
        return "named"
    return "default"


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
    from .plan import parse_acting_team

    acting_team = parse_acting_team(q_user)
    # 本轮视角队（角色扮演）优先于提问者主队，仅影响本轮检索扩召回
    scope_team = acting_team or team

    if step.tool == "crm.search":
        q_plan = str(args.get("query") or "").strip()
        if not q_plan and resolved:
            q_plan = resolved[0]
        elif not q_plan:
            q_plan = q_user[:160]
        mode = str(args.get("mode") or "auto").strip().lower()
        # 计数/交叉留整句给下游解析时间窗，不截成短关键词；意图由 planner 决定
        if mode in ("stats", "cross"):
            q_plan = q_user[:160]
        args["query"] = q_plan[:160]
        args.setdefault("mode", "auto")
        return args

    if step.tool.startswith("ask."):
        q_plan = str(args.get("query") or args.get("q") or "").strip()
        # Planner 已按工作记忆改写检索词；原话只是兜底。
        # 历史实现 q_user or q_plan 会让「他后来呢」这类原话覆盖 Planner 改写。
        q_full = q_plan or q_user
        if acting_team:
            from .plan import _ACTING_TEAM_RE as _re_act

            bare = _re_act.sub("", q_full).strip(" ，,。.?？") or q_full
            if acting_team not in bare:
                q_full = f"{acting_team} {bare}"
            else:
                q_full = bare
        scope = _ask_person_scope(q_user or q_full, has_resolved=bool(resolved))
        names: list[str] = []
        # 计划里已带的人名保留
        for n in args.get("person_names") or []:
            s = str(n).strip()
            if s and s not in names:
                names.append(s)
        for n in resolved:
            if n not in names:
                names.append(n)
        # prior 人名：self 范围不加（避免「和我相关」被上一轮组织列表污染）
        # 指定了其他队视角时也不灌 prior（常是硅谷人名残留）
        if scope != "self" and not acting_team:
            for n in _names_from_prior(prior):
                if n not in names:
                    names.append(n)
        # 点名同事（named）只扩被点名的人，不要把提问者自己塞进检索
        # self / team / default 才钉本人，保证「和我相关」能召回
        # 角色扮演其他队时不钉提问者本人
        if scope != "named" and name and name not in names and not acting_team:
            names = [name] + names
        if scope == "team" and scope_team:
            try:
                from ..feishu_hands import org_directory as od

                teammates, _label = od.our_team_members(scope_team, limit=12)
            except Exception:
                teammates = []
            for t in teammates:
                if t and t not in names:
                    names.append(t)
        # self / default / named：不灌整棵子树；default 仍可带 prior 人名
        args["query"] = q_full
        # 用户原话额外带上，供成文/时间语义使用；检索侧 adapters 会切掉这段
        if q_user and q_user != q_full:
            args["q"] = f"{q_full}\n\n{_CLUE_MARK}】{q_user}"[:500]
        else:
            args["q"] = q_full
        if names:
            args["person_names"] = names[:16]
        # 显式桶过滤：用户指定视角队 → 按该队周报桶检索
        if acting_team:
            args["team"] = acting_team
            args["apply_team_focus"] = True
        elif str(args.get("team") or args.get("team_filter") or "").strip():
            args.setdefault("apply_team_focus", False)
        log.info(
            "ask enrich scope=%s team=%s acting=%s person_names=%s q_len=%s",
            scope,
            scope_team or "-",
            acting_team or "-",
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
        # 保留 claim 级证据：Supervisor 收尾时会汇总回填 SupervisorResult，
        # 否则 answer_status/evidence 恒为空，质量采集与「每句有据」都失效。
        bindings = list(_bindings or [])
        evidence = list(_ev or [])
        ok = bool(getattr(result, "ok", False))
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        tier = "feishu_live" if str(step.tool).startswith("feishu.") else "published"
        if str(step.tool) == "crm.search":
            tier = "crm_prior"
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
            # stats/cross 的 empty=总数为0 仍是有效答案，不要重规划去灌周报
            if str(payload.get("mode") or "") in ("stats", "cross"):
                need_replan = False
                replan_reason = ""
            else:
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
            claim_bindings=bindings,
            evidence_refs=evidence,
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
