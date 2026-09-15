"""Agent Tool → Mesh 真实 Published 能力（非 stub）。

ask.published      → ask_engine.prepare（Published-only AskScope）
ask.relations_summary → published_json + relation_display.reader_visible

不在此扩 Planner/ReAct；Answer 优先用 prepare.direct_answer，
否则用检索上下文拼可追溯摘要（避免 Agent 路径强依赖 LLM 才能验收契约）。
可选：环境 MESH_AGENT_USE_LLM=1 时再调 llm 成文。
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..ask_scope import AskScope
from .. import ask_engine, relation_display
from .models import (
    AgentContext,
    ClaimBinding,
    IdentityResult,
    PermissionDecision,
    ToolResult,
)


def _deny_local(tool_id: str, reason: str) -> ToolResult:
    return ToolResult(ok=False, tool_id=tool_id, denied=True, error=reason)


def scope_from_agent(
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    con=None,
    *,
    query: str = "",
    team_filter: str = "",
    apply_team_focus: bool = False,
) -> tuple[AskScope, dict]:
    """构建 AskScope：IssueRef ⊥ TimeWindow（Context 锚点 ≠ 检索硬锁期次）。

    team_focus 只是权限侧「默认关注队」，不是周报桶硬 ACL。
    只有显式 team_filter / apply_team_focus=True 时才按桶过滤；
    「我们团队」应按飞书子树人名扩召回，不能用 Mesh 队名砍掉其他桶条目。
    """
    from . import temporal as temporal_mod

    qs = permission.query_scope or context.query_scope or {}
    focus = (qs.get("team_focus") or "") if isinstance(qs, dict) else ""
    team = (team_filter or "").strip()
    if not team and apply_team_focus:
        team = (focus or "").strip()
    context_slug = ""
    issue_date_from = None
    issue_date_to = None
    issue_mode = ""
    if context.issue_ref and context.issue_ref.mode != "none":
        context_slug = context.issue_ref.slug or ""
        issue_mode = context.issue_ref.mode or ""
        if con is not None and context_slug:
            row = con.execute(
                "SELECT date_start, date_end FROM issues WHERE slug=?", (context_slug,)
            ).fetchone()
            if row:
                issue_date_from = (row["date_start"] or "").strip() or None
                issue_date_to = (row["date_end"] or "").strip() or None
    q = query or context.text or ""
    sem = temporal_mod.resolve_time_semantics(
        q,
        issue_mode=issue_mode,
        issue_slug=context_slug,
        has_event_time=False,
    )
    # 检索 slug：time_window 清空 → 跨已发布；Context IssueRef 仍保留在 sem.issue_slug
    slug = temporal_mod.retrieval_slug_for_scope(sem, context_slug)
    date_from, date_to = temporal_mod.apply_time_filter_to_dates(
        sem,
        issue_date_from=issue_date_from,
        issue_date_to=issue_date_to,
        query=q,
    )
    scope = AskScope(
        channel=context.channel or identity.channel or "web",
        slug=slug,
        team=team or "",
        date_from=date_from,
        date_to=date_to,
        user_id=identity.mesh_user_id,
        feishu_open_id=identity.feishu_open_id or "",
        chat_id=context.chat_id or "",
        thread_id=context.thread_id or "",
        web_session_id=context.session_id or "",
        role=identity.mesh_role or "viewer",
        user_team=identity.primary_team or "",
    )
    return scope, sem.to_dict()


_CLUE_MARK = "【检索线索"


def _ctx_key(ctx: dict) -> str:
    if not isinstance(ctx, dict):
        return ""
    iid = ctx.get("条目ID") or ctx.get("item_id")
    if iid is not None and str(iid).strip():
        return f"item:{iid}"
    cid = ctx.get("chunk_id") or ctx.get("chunkId")
    if cid:
        return f"chunk:{cid}"
    title = str(ctx.get("标题") or ctx.get("title") or "").strip()
    issue = str(ctx.get("期号") or ctx.get("issue") or "").strip()
    body = str(ctx.get("内容") or ctx.get("body") or "").strip()[:80]
    return f"t:{issue}:{title}:{body}"


def _merge_contexts(*groups: list[dict], limit: int = 48) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for ctx in group or []:
            if not isinstance(ctx, dict):
                continue
            sec = str(ctx.get("章节") or ctx.get("section") or "")
            if sec in ("检索范围", "查询说明", "检索说明"):
                # 保留第一组的范围说明即可
                key = f"meta:{sec}:{ctx.get('标题')}"
            else:
                key = _ctx_key(ctx)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(ctx)
            if len(out) >= limit:
                return out
    return out


def _expand_person_contexts(
    con,
    scope: AskScope,
    q: str,
    person_names: list[str],
) -> list[dict]:
    """人名扩召回：分批独立 prepare，避免长串人名挤爆单次 FTS。"""
    names = [n for n in person_names if n and n not in (q or "")]
    if not names:
        return []
    merged: list[dict] = []
    # 每批 ≤3 个名；最多 4 批，控制成本
    batches = [names[i : i + 3] for i in range(0, min(len(names), 12), 3)]
    for batch in batches[:4]:
        sq = " ".join(batch)
        try:
            prep = ask_engine.prepare(con, q, scope, search_q=sq)
        except Exception:
            continue
        merged.extend(list(prep.get("contexts") or []))
    return merged


def _evidence_from_contexts(contexts: list[dict], slug: str) -> list[str]:
    refs: list[str] = []
    for i, ctx in enumerate(contexts or []):
        if not isinstance(ctx, dict):
            continue
        sec = str(ctx.get("章节") or ctx.get("section") or "")
        if sec in ("检索范围", "查询说明", "检索说明"):
            continue
        cid = ctx.get("chunk_id") or ctx.get("chunkId")
        iid = ctx.get("条目ID") or ctx.get("item_id")
        if cid:
            refs.append(f"ev:chunk:{cid}")
        elif iid is not None and str(iid).strip():
            refs.append(f"ev:item:{iid}")
        else:
            issue = str(ctx.get("期号") or ctx.get("issue") or slug or "")
            refs.append(f"ev:ctx:{issue or 'na'}:{i}")
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out[:40]


def _summary_from_contexts(contexts: list[dict], q: str, *, max_items: int = 5) -> str:
    lines: list[str] = []
    for ctx in (contexts or [])[:max_items]:
        if not isinstance(ctx, dict):
            continue
        sec = str(ctx.get("章节") or "")
        if sec in ("检索范围", "查询说明", "检索说明"):
            continue
        title = (ctx.get("标题") or ctx.get("title") or "").strip()
        body = (ctx.get("内容") or ctx.get("body") or "").strip()
        issue = (ctx.get("期号") or "").strip()
        bit = title or body[:80]
        if not bit:
            continue
        prefix = f"[{issue}] " if issue else ""
        lines.append(f"- {prefix}{bit}")
    if not lines:
        return "未在已上线周报中找到与问题直接相关的记录。"
    head = f"根据已上线语料（与「{q[:40]}」相关）："
    return head + "\n" + "\n".join(lines)


def _maybe_llm_answer(
    q: str, contexts: list[dict], *, temporal_block: str = ""
) -> str | None:
    if os.environ.get("MESH_AGENT_USE_LLM", "").strip() not in ("1", "true", "yes"):
        return None
    try:
        from .. import llm

        # Agent 成文：Top-N 由 llm.pack_answer_contexts / MESH_ANSWER_CTX_N 统一裁剪
        ctxs = [c for c in (contexts or []) if isinstance(c, dict)]
        ans = llm.answer_question(q, ctxs, temporal_block=temporal_block)
        if isinstance(ans, dict):
            return (ans.get("answer") or ans.get("text") or "").strip() or None
        return (str(ans) if ans else None) or None
    except Exception:
        return None


def ask_published(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any],
) -> ToolResult:
    """真实 Published 检索 + Temporal Phase 1 成文约束。"""
    from . import temporal as temporal_mod
    from .temporal import TimeSemantics

    slug = (context.issue_ref.slug if context.issue_ref else "") or ""
    if context.issue_ref and context.issue_ref.mode != "none":
        row = con.execute(
            "SELECT status, published_json FROM issues WHERE slug=?", (slug,)
        ).fetchone()
        if not row or row["status"] != "published" or not (row["published_json"] or "").strip():
            return _deny_local("ask.published", "issue_not_published")
    elif not slug:
        return ToolResult(
            ok=True,
            tool_id="ask.published",
            payload={
                "answer": "当前没有可引用的已上线期次。",
                "mode": "none",
                "n_hits": 0,
            },
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim="无可用 IssueRef",
                    evidence_refs=[],
                    status="unsupported",
                    reason="no_issue_ref",
                )
            ],
        )

    # Supervisor 写 query；旧路径写 q。二者都要认。
    raw = str(args.get("q") or args.get("query") or context.text or "").strip()
    user_q = raw
    if _CLUE_MARK in raw:
        user_q = raw.split(_CLUE_MARK, 1)[0].strip() or raw
    person_names = [
        str(n).strip()
        for n in (args.get("person_names") or [])
        if str(n or "").strip()
    ]
    q = user_q  # 时间语义 / claim / 成文都以用户原话为准
    # 主检索只用用户原话；人名走分批扩召回合并（禁止把整棵子树塞进一条 FTS）
    search_q = user_q

    explicit_team = str(
        args.get("team") or args.get("team_filter") or args.get("owner_team") or ""
    ).strip()
    apply_focus = bool(args.get("apply_team_focus")) or bool(args.get("my_team"))
    scope, sem_dict = scope_from_agent(
        identity,
        permission,
        context,
        con=con,
        query=q,
        team_filter=explicit_team,
        apply_team_focus=apply_focus,
    )
    sem = TimeSemantics(**{k: sem_dict[k] for k in TimeSemantics.__dataclass_fields__})

    early = temporal_mod.maybe_direct_answer(q, sem)
    if early:
        return ToolResult(
            ok=True,
            tool_id="ask.published",
            payload={
                "answer": early,
                "mode": "temporal_direct",
                "n_hits": 0,
                "issue": slug,
                "llm_used": False,
                "temporal": sem.to_dict(),
            },
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim=early[:500],
                    evidence_refs=[],
                    status="grounded",
                    reason="temporal_hard_rule",
                )
            ],
        )

    prepared = ask_engine.prepare(con, q, scope, search_q=search_q)
    primary_ctxs = list(prepared.get("contexts") or [])
    expand_ctxs = _expand_person_contexts(con, scope, q, person_names)
    contexts = _merge_contexts(primary_ctxs, expand_ctxs)
    from . import claim_support as claim_support_mod

    contexts = claim_support_mod.enrich_contexts_for_denial_counter_evidence(
        con, scope, q, contexts
    )
    evidence = _evidence_from_contexts(contexts, slug)
    n_hits = max(
        int(prepared.get("n_hits") or prepared.get("n_context") or 0),
        len([c for c in contexts if isinstance(c, dict) and str(c.get("章节") or "") not in ("检索范围", "查询说明", "检索说明")]),
    )

    support_assess = claim_support_mod.assess_claim_support(
        q, contexts=contexts, evidence_refs=evidence
    )
    abstain = claim_support_mod.abstain_answer_for_unsupported_claim(support_assess)
    if abstain:
        return ToolResult(
            ok=True,
            tool_id="ask.published",
            payload={
                "answer": abstain,
                "mode": "claim_support_abstain",
                "n_hits": n_hits,
                "issue": slug,
                "llm_used": False,
                "temporal": sem.to_dict(),
                "claim_support": support_assess,
            },
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim=abstain[:500],
                    evidence_refs=[],
                    status="unsupported",
                    reason=support_assess.get("reason") or "claim_unsupported",
                )
            ],
        )

    llm_used = False
    tblock = temporal_mod.prompt_block(sem)
    answer = (prepared.get("direct_answer") or "").strip()
    if not answer and contexts:
        llm_ans = _maybe_llm_answer(q, contexts, temporal_block=tblock)
        if llm_ans:
            answer = llm_ans
            llm_used = True
        else:
            answer = _summary_from_contexts(contexts, q)
    if not answer:
        answer = "未在已上线周报中找到与问题直接相关的记录。"

    answer = temporal_mod.apply_hard_rules(answer, sem)

    if n_hits > 0 and evidence:
        status = "grounded"
    elif contexts:
        status = "weak"
    else:
        status = "unsupported"

    binding = ClaimBinding(
        claim=answer[:500],
        evidence_refs=evidence[:8],
        status=status,
        reason=f"ask_engine:{prepared.get('mode') or 'unknown'}{'+llm' if llm_used else ''}",
    )
    return ToolResult(
        ok=True,
        tool_id="ask.published",
        payload={
            "answer": answer,
            "mode": prepared.get("mode") or "hybrid",
            "n_hits": n_hits,
            "n_context": prepared.get("n_context"),
            "issue": slug,
            "search_q": prepared.get("search_q") or q,
            "latency_ms": prepared.get("latency_ms"),
            "team_scope": prepared.get("team_scope") or scope.team_filter,
            "llm_used": llm_used,
            "temporal": sem.to_dict(),
            "date_from": scope.date_from,
            "date_to": scope.date_to,
            "claim_support": support_assess,
        },
        evidence_refs=evidence,
        claim_bindings=[binding] if status != "unsupported" else [
            ClaimBinding(
                claim=answer[:500],
                evidence_refs=[],
                status="unsupported",
                reason=binding.reason,
            )
        ],
    )


def _relation_evidence_refs(rel: dict, slug: str, idx: int) -> list[str]:
    refs: list[str] = []
    for j, ev in enumerate(rel.get("evidence") or []):
        if isinstance(ev, dict):
            iid = ev.get("item_id") or ev.get("id")
            if iid is not None and str(iid).strip():
                refs.append(f"ev:item:{iid}")
                continue
            cid = ev.get("chunk_id")
            if cid:
                refs.append(f"ev:chunk:{cid}")
                continue
        refs.append(f"ev:rel:{slug}:{idx}:{j}")
    if not refs:
        refs.append(f"ev:rel:{slug}:{idx}")
    return refs


def ask_relations_summary(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any],
) -> ToolResult:
    """真实 Published 关系摘要（reader_visible 口径）。"""
    slug = (context.issue_ref.slug if context.issue_ref else "") or ""
    if not slug or (context.issue_ref and context.issue_ref.mode == "none"):
        return ToolResult(
            ok=True,
            tool_id="ask.relations_summary",
            payload={"answer": "没有可引用的已上线期次，无法汇总关系。", "relations": []},
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim="无 IssueRef",
                    evidence_refs=[],
                    status="unsupported",
                    reason="no_issue_ref",
                )
            ],
        )

    row = con.execute(
        "SELECT status, published_json FROM issues WHERE slug=?", (slug,)
    ).fetchone()
    if not row or row["status"] != "published" or not (row["published_json"] or "").strip():
        return _deny_local("ask.relations_summary", "issue_not_published")

    try:
        data = json.loads(row["published_json"] or "{}")
    except Exception:
        data = {}

    raw = list(data.get("relations") or [])
    # 与 Publish 读者口径一致
    reader, _backlog = relation_display.split_relations_for_publish(raw)
    # 若旧数据无完整卡，仍允许有 title 的 published 关系进入摘要（弱）
    if not reader:
        reader = [
            r for r in raw
            if isinstance(r, dict)
            and (r.get("title") or r.get("body") or r.get("label"))
            and (r.get("decision_tier") or "").lower() != "skip"
        ]

    team_focus = (permission.query_scope or {}).get("team_focus") or ""
    if team_focus:
        filtered = []
        for r in reader:
            teams = r.get("teams") or []
            badges = r.get("team_badges") or []
            blob = " ".join(str(x) for x in list(teams) + list(badges))
            if team_focus in blob or team_focus in (r.get("body") or "") or team_focus in (r.get("title") or ""):
                filtered.append(r)
        # 聚焦过滤为空时仍返回全集摘要说明（不二次 Tool）
        if filtered:
            reader = filtered

    evidence: list[str] = []
    cards: list[dict] = []
    for i, rel in enumerate(reader[:20]):
        refs = _relation_evidence_refs(rel, slug, i)
        evidence.extend(refs)
        cards.append(
            {
                "title": (rel.get("title") or "").strip(),
                "label": (rel.get("label") or "").strip(),
                "body": (rel.get("body") or "").strip()[:240],
                "decision_tier": rel.get("decision_tier") or "",
                "evidence_refs": refs,
            }
        )

    # 去重 evidence
    seen: set[str] = set()
    ev_out: list[str] = []
    for r in evidence:
        if r not in seen:
            seen.add(r)
            ev_out.append(r)

    if cards:
        lines = []
        for c in cards[:8]:
            label = c["label"] or c["decision_tier"] or "关系"
            title = c["title"] or c["body"][:40] or "(无标题)"
            lines.append(f"- [{label}] {title}")
        answer = f"期次 {slug} 已上线关系（{len(cards)}）：\n" + "\n".join(lines)
        if any((r.get("evidence") or []) for r in reader[:20]):
            status = "grounded"
        else:
            status = "weak"
    else:
        answer = f"期次 {slug} 的已上线周报中暂无读者可见关系卡。"
        status = "unsupported"

    binding = ClaimBinding(
        claim=answer[:500],
        evidence_refs=ev_out[:8],
        status=status,
        reason="published_relations_reader_visible",
    )
    return ToolResult(
        ok=True,
        tool_id="ask.relations_summary",
        payload={
            "answer": answer,
            "relations": cards,
            "count": len(cards),
            "issue": slug,
            "team_focus": team_focus or None,
            "claim_support": {
                "support": "supported" if status == "grounded" else "insufficient",
                "reason": binding.reason,
            },
        },
        evidence_refs=ev_out,
        claim_bindings=[binding],
    )
