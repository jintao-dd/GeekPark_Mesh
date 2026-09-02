"""多步交叉分析引擎（蓝图 Phase A）。

契约：docs/ASK_ANALYSIS_CONTRACT.md
步骤：route → retrieve → group → source* → cross? → verify →（调用方发 token）
每步带 status: running|completed|skipped|error|timeout。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from collections import defaultdict
from typing import Any, Generator

from . import ask_citation, ask_concurrency, ask_context, ask_protocol, ask_store, db, llm

_MAX_GROUPS = max(1, int(os.environ.get("MESH_ANALYSIS_MAX_GROUPS", "4") or "4"))
_MAX_PER_GROUP = max(2, int(os.environ.get("MESH_ANALYSIS_MAX_PER_GROUP", "6") or "6"))
_SOURCE_PARALLEL = max(1, int(os.environ.get("MESH_ANALYSIS_SOURCE_PARALLEL", "4") or "4"))
_SOURCE_TIMEOUT = max(20, int(os.environ.get("MESH_ANALYSIS_SOURCE_TIMEOUT", "75") or "75"))
_CROSS_TIMEOUT = max(20, int(os.environ.get("MESH_ANALYSIS_CROSS_TIMEOUT", "90") or "90"))
_COMPOSE_TIMEOUT = max(20, int(os.environ.get("MESH_ANALYSIS_COMPOSE_TIMEOUT", "75") or "75"))


def analysis_enabled(payload: dict | None = None) -> bool:
    if payload is not None and "analysis" in payload:
        v = payload.get("analysis")
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off", "")
    v = (os.environ.get("MESH_ASK_ANALYSIS", "1") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _is_meta_ctx(ctx: dict) -> bool:
    sec = str(ctx.get("章节") or ctx.get("section") or "")
    issue = str(ctx.get("期号") or ctx.get("issue") or "")
    title = str(ctx.get("标题") or ctx.get("title") or "")
    if sec in ("检索范围", "查询说明", "结构化检索"):
        return True
    if issue in ("查询说明", "检索范围"):
        return True
    if "事实表算出" in title:
        return True
    return False


def _ctx_key(ctx: dict) -> tuple[str, str]:
    team = (ctx.get("归属团队") or ctx.get("团队") or "").strip()
    layer = (ctx.get("来源层") or "").strip()
    issue = (ctx.get("期号") or ctx.get("issue") or "").strip() or "未知期"
    src = team or layer or "未标注来源"
    return src, issue


def group_contexts(contexts: list[dict] | None) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ctx in contexts or []:
        if not isinstance(ctx, dict) or _is_meta_ctx(ctx):
            continue
        buckets[_ctx_key(ctx)].append(ctx)

    groups: list[dict[str, Any]] = []
    for (src, issue), items in buckets.items():
        groups.append({
            "source": src,
            "issue": issue,
            "n": len(items),
            "items": items[:_MAX_PER_GROUP],
        })
    groups.sort(key=lambda g: (-g["n"], g["source"], g["issue"]))
    out = []
    for i, g in enumerate(groups[:_MAX_GROUPS]):
        gid = f"g{i+1}"
        g = dict(g)
        g["id"] = gid
        g["group_id"] = gid
        out.append(g)
    return out


def _format_group_blob(group: dict) -> str:
    lines = []
    for j, it in enumerate(group.get("items") or [], 1):
        title = (it.get("标题") or it.get("title") or "").strip()
        body = (it.get("内容") or it.get("body") or "").strip()[:400]
        sec = (it.get("章节") or "").strip()
        item_id = it.get("条目ID") or it.get("item_id") or ""
        lines.append(f"[e{j}] sec={sec} title={title} item_id={item_id}\n{body}")
    return "\n\n".join(lines)


def _run_with_timeout(fn, timeout: float, fallback):
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            fut.cancel()
            try:
                return fallback(TimeoutError("timeout"))
            except TypeError:
                out = fallback()
                if isinstance(out, dict):
                    out.setdefault("_fallback", True)
                    out.setdefault("_error", "timeout")
                return out
        except Exception as e:
            try:
                return fallback(e)
            except TypeError:
                return fallback()


def _analyze_one_source(q: str, group: dict) -> dict:
    system = (
        "你是极客公园 Mesh 的单源素材抽取器。只根据【本源】证据抽取，禁止跨源推断。\n"
        "输出 JSON：{\"facts\":[{\"text\":\"...\",\"evidence_refs\":[\"e1\"]}],"
        "\"entities\":[\"...\"],\"events\":[{\"text\":\"...\",\"evidence_refs\":[\"e1\"]}],"
        "\"evidence\":[{\"ref\":\"e1\",\"quote\":\"...\"}]}\n"
        "无依据则数组留空。"
    )
    user = (
        f"用户问题（仅作关注点）：{q}\n"
        f"来源：{group['source']} · 期号：{group['issue']}\n\n"
        f"本源证据：\n{_format_group_blob(group)}\n"
    )

    def _call():
        with ask_concurrency.llm_slot(pool="ask"):
            raw = llm.call(system, user, max_tokens=900, json_mode=True)
        return raw if isinstance(raw, dict) else json.loads(str(raw) or "{}")

    def _fb(err=None):
        evidence, facts, entities = [], [], []
        for j, it in enumerate(group.get("items") or [], 1):
            quote = ((it.get("内容") or it.get("body") or "")[:160]).strip()
            title = (it.get("标题") or it.get("title") or "").strip()
            if quote:
                iid = it.get("条目ID") or it.get("item_id")
                cid = it.get("chunk_id") or it.get("chunkId")
                evidence.append({
                    "ref": f"e{j}", "quote": quote,
                    "item_id": iid, "chunk_id": cid,
                })
                facts.append({"text": title or quote[:80], "evidence_refs": [f"e{j}"]})
            if title:
                entities.append(title.split("·")[0].strip() or title)
        return {
            "facts": facts[:8],
            "entities": entities[:12],
            "events": [],
            "evidence": evidence,
            "_fallback": True,
            "_error": (
                "timeout"
                if (err is None or isinstance(err, TimeoutError) or "timeout" in str(err).lower())
                else str(err)[:120]
            ),
        }

    data = _run_with_timeout(_call, _SOURCE_TIMEOUT, _fb)
    if not isinstance(data, dict):
        data = _fb()

    facts = data.get("facts") if isinstance(data.get("facts"), list) else []
    entities = data.get("entities") if isinstance(data.get("entities"), list) else []
    events = data.get("events") if isinstance(data.get("events"), list) else []
    evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    if not evidence:
        for j, it in enumerate(group.get("items") or [], 1):
            quote = ((it.get("内容") or it.get("body") or "")[:160]).strip()
            if quote:
                evidence.append({
                    "ref": f"e{j}", "quote": quote,
                    "item_id": it.get("条目ID") or it.get("item_id"),
                    "chunk_id": it.get("chunk_id") or it.get("chunkId"),
                })

    return {
        "group_id": group["group_id"],
        "source": group["source"],
        "issue": group["issue"],
        "n_evidence": group["n"],
        "facts": facts,
        "entities": [str(x) for x in entities if str(x).strip()][:20],
        "events": events,
        "evidence": evidence,
        "items": group.get("items") or [],
        "_fallback": bool(data.get("_fallback")),
        "_error": data.get("_error"),
    }


def _reports_from_structured_contexts(contexts: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ctx in contexts or []:
        if not isinstance(ctx, dict) or _is_meta_ctx(ctx):
            continue
        team = (ctx.get("归属团队") or ctx.get("团队") or "未标注来源").strip()
        issue = (ctx.get("期号") or "未知期").strip()
        buckets[(team, issue)].append(ctx)
    reports = []
    for i, ((team, issue), items) in enumerate(list(buckets.items())[:_MAX_GROUPS]):
        gid = f"g{i+1}"
        facts, entities, evidence = [], [], []
        for j, it in enumerate(items[:_MAX_PER_GROUP], 1):
            title = (it.get("标题") or "").strip()
            body = (it.get("内容") or "").strip()
            text = title or body[:80]
            if text:
                facts.append({"text": text, "evidence_refs": [f"e{j}"]})
            if title:
                entities.append(title.split("·")[0].strip() or title)
            if body:
                evidence.append({
                    "ref": f"e{j}", "quote": body[:160],
                    "item_id": it.get("条目ID") or it.get("item_id"),
                    "chunk_id": it.get("chunk_id") or it.get("chunkId"),
                })
        reports.append({
            "group_id": gid,
            "source": team,
            "issue": issue,
            "n_evidence": len(items),
            "facts": facts,
            "entities": entities[:20],
            "events": [],
            "evidence": evidence,
            "items": items[:_MAX_PER_GROUP],
            "_from_structured": True,
        })
    return reports


def _heuristic_cross(q: str, source_reports: list[dict]) -> dict:
    if not source_reports:
        return {"summary": "无来源可交叉", "claims": [], "conflicts": [], "corroborations": []}
    if len(source_reports) == 1:
        only = source_reports[0]
        claims = []
        for f in only.get("facts") or []:
            if isinstance(f, dict) and f.get("text"):
                claims.append({"text": f["text"], "support": [only["group_id"]], "kind": "single"})
        return {
            "summary": f"单来源「{only.get('source')}」事实汇总。",
            "same_entities": only.get("entities") or [],
            "claims": claims,
            "conflicts": [],
            "corroborations": [],
            "changes": [],
        }

    ent_sets = []
    for r in source_reports:
        ent_sets.append({str(x).strip() for x in (r.get("entities") or []) if str(x).strip()})
    common = set.intersection(*ent_sets) if ent_sets else set()
    claims = []
    for name in sorted(common)[:30]:
        support = [r["group_id"] for r in source_reports if name in (r.get("entities") or [])]
        claims.append({
            "text": f"「{name}」在多个来源中均有记录",
            "support": support,
            "kind": "corroborated",
        })
    return {
        "summary": f"启发式交叉：共有实体 {len(common)} 个（模型交叉超时或跳过，已用实体交集兜底）。",
        "same_entities": sorted(common)[:30],
        "claims": claims,
        "conflicts": [],
        "corroborations": [
            {"text": n, "sources": [r["group_id"] for r in source_reports]}
            for n in sorted(common)[:15]
        ],
        "changes": [],
        "_fallback": True,
    }


def _cross_analyze(q: str, source_reports: list[dict]) -> dict:
    if len(source_reports) <= 1:
        return _heuristic_cross(q, source_reports)

    system = (
        "你是极客公园 Mesh 的多源交叉分析员。输入是各来源已抽取结果。\n"
        "找出：相同实体、相同事件、时间关系、因果/关联、多源印证、信息冲突、新增变化。\n"
        "禁止编造分源中没有的事实。\n"
        "输出 JSON：{\"summary\":\"...\",\"same_entities\":[],\"same_events\":[],"
        "\"time_links\":[],\"causal_links\":[],"
        "\"corroborations\":[{\"text\":\"...\",\"sources\":[\"g1\",\"g2\"]}],"
        "\"conflicts\":[{\"text\":\"...\",\"sources\":[\"g1\",\"g2\"]}],"
        "\"changes\":[],"
        "\"claims\":[{\"text\":\"拟写入最终回答的断言\",\"support\":[\"g1\"],"
        "\"kind\":\"corroborated|single|conflict_note\"}]}"
    )
    slim = []
    for r in source_reports:
        slim.append({
            "group_id": r.get("group_id"),
            "source": r.get("source"),
            "issue": r.get("issue"),
            "facts": (r.get("facts") or [])[:12],
            "entities": (r.get("entities") or [])[:20],
            "events": (r.get("events") or [])[:8],
        })
    user = f"用户问题：{q}\n\n分源结构化结果：\n{json.dumps(slim, ensure_ascii=False)[:6000]}\n"

    def _call():
        with ask_concurrency.llm_slot(pool="ask"):
            raw = llm.call(system, user, max_tokens=1400, json_mode=True)
        data = raw if isinstance(raw, dict) else json.loads(str(raw) or "{}")
        if not isinstance(data.get("claims"), list):
            data["claims"] = []
        return data

    def _fb(err=None):
        out = _heuristic_cross(q, source_reports)
        out["_fallback"] = True
        if err is not None and (isinstance(err, TimeoutError) or "timeout" in str(err).lower()):
            out["_error"] = "timeout"
        elif err is not None:
            out["_error"] = str(err)[:120]
        else:
            out["_error"] = "timeout"
        return out

    return _run_with_timeout(_call, _CROSS_TIMEOUT, _fb)


def _evidence_index(source_reports: list[dict]) -> dict[str, dict]:
    """ref -> {quote, item_id, chunk_id}"""
    idx: dict[str, dict] = {}
    for r in source_reports or []:
        gid = str(r.get("group_id") or "")
        items = r.get("items") or []
        for j, it in enumerate(items, 1):
            ref = f"e{j}"
            quote = ((it.get("内容") or it.get("body") or "")[:400]).strip()
            iid = it.get("条目ID") or it.get("item_id")
            cid = it.get("chunk_id") or it.get("chunkId")
            idx[ref] = {"quote": quote, "item_id": iid, "chunk_id": cid}
            if gid:
                idx[f"{gid}:{ref}"] = idx[ref]
        for ev in r.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            ref = str(ev.get("ref") or "").strip()
            if not ref:
                continue
            row = dict(idx.get(ref) or {})
            row["quote"] = str(ev.get("quote") or row.get("quote") or "").strip()
            if ev.get("item_id") is not None:
                row["item_id"] = ev.get("item_id")
            if ev.get("chunk_id"):
                row["chunk_id"] = ev.get("chunk_id")
            idx[ref] = row
            if gid:
                idx[f"{gid}:{ref}"] = row
    return idx


def _context_id_index(contexts: list[dict]) -> tuple[dict, dict]:
    by_item: dict[str, str] = {}
    by_chunk: dict[str, str] = {}
    for ctx in contexts or []:
        if _is_meta_ctx(ctx):
            continue
        blob = f"{ctx.get('标题') or ''}\n{ctx.get('内容') or ''}"
        iid = ctx.get("条目ID") or ctx.get("item_id")
        cid = ctx.get("chunk_id") or ctx.get("chunkId")
        if iid is not None and str(iid).strip():
            by_item[str(iid)] = blob
        if cid:
            by_chunk[str(cid)] = blob
    return by_item, by_chunk


def _text_supported_by_blob(text: str, blob: str) -> bool:
    t = re.sub(r"\s+", "", (text or "").strip())
    b = re.sub(r"\s+", "", (blob or "").strip())
    if not t or not b:
        return False
    if t in b or t[:12] in b or b[:24] in t:
        return True
    # 2-gram 重叠（中文 paraphrase 比整词切分更稳）
    def _grams(s: str) -> set[str]:
        s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)
        return {s[i : i + 2] for i in range(max(0, len(s) - 1))}

    ga, gb = _grams(t), _grams(b)
    if not ga:
        return False
    overlap = len(ga & gb) / len(ga)
    return overlap >= 0.22


def _claim_supported(
    text: str,
    claim: dict,
    contexts: list[dict],
    source_reports: list[dict],
) -> str:
    t = (text or "").strip()
    if not t or len(t) < 4:
        return "reject"
    ev_idx = _evidence_index(source_reports)
    by_item, by_chunk = _context_id_index(contexts)
    refs = claim.get("evidence_refs") if isinstance(claim.get("evidence_refs"), list) else []
    if refs and ev_idx:
        bound = 0
        for ref in refs:
            rkey = str(ref)
            meta = ev_idx.get(rkey) or ev_idx.get(rkey.split(":")[-1]) or {}
            rq = str(meta.get("quote") or "")
            iid = meta.get("item_id")
            cid = meta.get("chunk_id")
            if iid is not None and str(iid) in by_item:
                if _text_supported_by_blob(t, by_item[str(iid)]):
                    bound += 1
                    continue
            if cid and str(cid) in by_chunk:
                if _text_supported_by_blob(t, by_chunk[str(cid)]):
                    bound += 1
                    continue
            if not rq:
                continue
            rq_l = rq.lower()
            if any(w in rq_l for w in re.findall(r"[\u4e00-\u9fff]{2,}", t)[:8]):
                bound += 1
            elif t[:20].lower() in rq_l or rq[:40].lower() in t.lower():
                bound += 1
        if bound >= max(1, len(refs) // 2):
            return "keep"
        if bound == 0:
            return "reject"
        return "downgrade"
    blob_parts = []
    for ctx in (contexts or [])[:24]:
        if _is_meta_ctx(ctx):
            continue
        blob_parts.append(str(ctx.get("内容") or ctx.get("body") or ""))
        blob_parts.append(str(ctx.get("标题") or ""))
    for r in source_reports:
        for f in r.get("facts") or []:
            if isinstance(f, dict):
                blob_parts.append(str(f.get("text") or ""))
        for ev in r.get("evidence") or []:
            if isinstance(ev, dict):
                blob_parts.append(str(ev.get("quote") or ""))
    blob_l = "\n".join(blob_parts).lower()
    tokens = re.findall(r"[\u4e00-\u9fff]{2,6}", t)
    if not tokens:
        return "downgrade"
    hits = sum(1 for w in tokens if w.lower() in blob_l)
    ratio = hits / max(1, len(tokens))
    if ratio >= 0.35:
        return "keep"
    if ratio >= 0.15:
        return "downgrade"
    return "reject"


def _scrub_user_answer(text: str) -> str:
    """去掉面向用户不该出现的内部话术。"""
    t = (text or "").strip()
    if not t:
        return t
    # 常见泄漏开头
    t = re.sub(
        r"^(根据已校验断言|依据已校验断言|基于已校验断言|已校验断言|根据断言|依据断言)"
        r"[，,：:\s]*",
        "",
        t,
    )
    t = re.sub(r"已校验断言", "", t)
    t = re.sub(r"【?可用要点】?[：:]\s*", "", t)
    t = re.sub(r"交叉摘要[：:]\s*", "", t)
    t = re.sub(r"(?m)^#{1,6}\s*", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _verify_and_compose(
    q: str,
    cross: dict,
    source_reports: list[dict],
    contexts: list[dict],
) -> tuple[str, dict]:
    claims_in = cross.get("claims") if isinstance(cross.get("claims"), list) else []
    if not claims_in:
        for r in source_reports:
            for f in r.get("facts") or []:
                if isinstance(f, dict) and f.get("text"):
                    claims_in.append({
                        "text": f["text"],
                        "support": [r.get("group_id")],
                        "kind": "single",
                    })

    verified_claims, downgraded, rejected = [], [], []
    for c in claims_in:
        if not isinstance(c, dict):
            continue
        text = (c.get("text") or "").strip()
        if not text:
            continue
        decision = _claim_supported(text, c, contexts, source_reports)
        row = dict(c)
        row["decision"] = decision
        if decision == "keep":
            verified_claims.append(row)
        elif decision == "downgrade":
            row["text"] = f"（待核对）{text}"
            downgraded.append(row)
        else:
            rejected.append(row)

    conflicts = cross.get("conflicts") or []
    src_line = "来源：" + "；".join(
        f"{r.get('issue')}·{r.get('source')}" for r in source_reports
    )
    draft_bits = []
    if cross.get("summary"):
        draft_bits.append(str(cross["summary"]).strip())
    for c in verified_claims:
        draft_bits.append(c["text"])
    for c in downgraded[:3]:
        draft_bits.append(c["text"])
    for cf in conflicts[:3]:
        if isinstance(cf, dict) and cf.get("text"):
            draft_bits.append(f"来源间存在不同记录：{cf['text']}")
        elif isinstance(cf, str):
            draft_bits.append(f"来源间存在不同记录：{cf}")
    draft = "\n".join(x for x in draft_bits if x)
    if source_reports:
        draft = (draft + "\n" + src_line).strip()

    system = (
        "你是极客公园 Mesh 写作者。把下列「可用要点」写成通顺中文回答。"
        "禁止新增事实；来源冲突时并列说明；文末保留「来源：」行。"
        "禁止使用 Markdown 标题（不要用 # ## ###）；用自然段落即可。"
        "禁止使用内部术语：不要写「断言」「已校验」「根据已校验断言」「交叉摘要」等字样，直接陈述事实。"
    )
    points = []
    for c in verified_claims + downgraded[:3]:
        if isinstance(c, dict) and c.get("text"):
            points.append({"text": c["text"]})
    user = (
        f"问题：{q}\n\n"
        f"可用要点：\n{json.dumps(points, ensure_ascii=False)[:3500]}\n\n"
        f"补充说明：{(cross.get('summary') or '')[:400]}\n"
        f"来源行：{src_line}\n"
    )

    def _call():
        with ask_concurrency.llm_slot(pool="ask"):
            out = llm.call(system, user, max_tokens=1400, json_mode=False)
        text = out.get("answer") if isinstance(out, dict) else str(out or "").strip()
        return text or draft

    text = _run_with_timeout(_call, _COMPOSE_TIMEOUT, lambda err=None: draft)
    text = _scrub_user_answer(text or "")
    grounded = ask_citation.validate_answer(text, [c for c in contexts if not _is_meta_ctx(c)])
    ans = _scrub_user_answer(grounded.get("answer") or text)
    cite_removed = grounded.get("removed") or []
    stats = {
        "verified": len(verified_claims),
        "rejected": len(rejected) + len(cite_removed),
        "downgraded": len(downgraded),
        "flags": grounded.get("flags") or [],
    }
    return ans, stats


def _persist_start(
    analysis_id: str,
    q: str,
    *,
    parent_analysis_id: str | None = None,
    session_id: str = "",
    user_id: int | None = None,
) -> None:
    try:
        con = db.connect()
        ask_store.start(
            con,
            analysis_id=analysis_id,
            question=q,
            session_id=session_id,
            user_id=user_id,
            parent_analysis_id=parent_analysis_id,
        )
        with db.write_lock():
            db.commit_retry(con)
        con.close()
    except Exception:
        pass


def _persist_finish(analysis_id: str, meta: dict) -> None:
    try:
        con = db.connect()
        ask_store.finish(
            con,
            analysis_id=analysis_id,
            answer=meta.get("answer") or "",
            context_refs=meta.get("context_refs"),
            usage=meta.get("usage"),
            verify=meta.get("verify"),
            sources=meta.get("sources"),
            status="completed",
        )
        with db.write_lock():
            db.commit_retry(con)
        con.close()
    except Exception:
        pass


def run_analysis(
    q: str,
    prepared: dict,
    *,
    history: list | None = None,
    persist: dict | None = None,
) -> Generator[dict[str, Any], None, dict[str, Any]]:
    # history 故意忽略：禁止历史 Answer / NL 分析结果进入本轮
    _ = history
    t0 = time.time()
    analysis_id = secrets.token_hex(8)
    report_id = analysis_id  # API 兼容
    persist_ctx = persist if isinstance(persist, dict) else {}
    _persist_start(
        analysis_id,
        q,
        parent_analysis_id=(prepared.get("context_route") or {}).get("parent_analysis_id")
        if isinstance(prepared.get("context_route"), dict)
        else None,
        session_id=str(persist_ctx.get("session_id") or ""),
        user_id=persist_ctx.get("user_id"),
    )
    usage = {"llm_calls": 0, "source_groups": 0, "elapsed_ms": 0, "path": "analysis", "cross": False}
    from app import llm as _llm_mod
    _llm_mod.reset_usage_accum()

    ctx_route = prepared.get("context_route")
    if not isinstance(ctx_route, dict):
        ctx_route = {"kind": "independent", "reason": "default", "context_refs": {}}
    kind = ctx_route.get("kind") or "independent"
    parent_id = ctx_route.get("parent_analysis_id")
    inherited = ctx_route.get("context_refs") if kind == "followup" else {}
    n_seeds = len((inherited or {}).get("chunk_ids") or []) if isinstance(inherited, dict) else 0
    yield ask_protocol.step_event(
        "route", "completed",
        analysis_id=analysis_id,
        message="独立问题" if kind == "independent" else f"追问 · refs seeds={n_seeds}",
        kind=kind,
        reason=ctx_route.get("reason") or "",
        parent_analysis_id=parent_id,
    )

    contexts = list(prepared.get("contexts") or [])
    yield ask_protocol.step_event(
        "retrieve", "completed",
        analysis_id=analysis_id,
        message=f"召回 {len(contexts)} 条",
        n_context=len(contexts),
    )

    # 结构化查询：跳过 LLM 分源，直接物化
    structured = (prepared.get("mode") or "") == "structured"
    if structured:
        source_reports = _reports_from_structured_contexts(contexts)
        groups = [
            {
                "group_id": r["group_id"],
                "source": r["source"],
                "issue": r["issue"],
                "n": r["n_evidence"],
            }
            for r in source_reports
        ]
        usage["path"] = "structured"
        usage["source_groups"] = len(groups)
        yield ask_protocol.step_event(
            "group", "completed",
            analysis_id=analysis_id,
            message=f"{len(groups)} 组",
            groups=groups,
        )
        for r in source_reports:
            yield ask_protocol.step_event(
                "source", "completed",
                analysis_id=analysis_id,
                message=r["source"],
                group_id=r["group_id"],
                source=r["source"],
                issue=r["issue"],
                facts=r.get("facts") or [],
                entities=r.get("entities") or [],
                events=[],
                evidence=r.get("evidence") or [],
            )
    else:
        groups = group_contexts(contexts)
        usage["source_groups"] = len(groups)
        yield ask_protocol.step_event(
            "group", "completed",
            analysis_id=analysis_id,
            message=f"{len(groups)} 组",
            groups=[
                {"group_id": g["group_id"], "source": g["source"], "issue": g["issue"], "n": g["n"]}
                for g in groups
            ],
        )
        if not groups:
            ans = prepared.get("direct_answer") or "可用记录中未找到相关材料。"
            yield ask_protocol.step_event(
                "verify", "completed",
                analysis_id=analysis_id,
                message="无分组证据",
                verified=0, rejected=0, downgraded=0,
            )
            usage["elapsed_ms"] = int((time.time() - t0) * 1000)
            for k, v in _llm_mod.take_usage_accum().items():
                usage[k] = v
            early = {
                "answer": ans, "analysis_id": analysis_id, "report_id": report_id, "usage": usage,
                "verify": {"verified": 0, "rejected": 0, "downgraded": 0},
                "cross": {}, "sources": [],
                "context_refs": ask_context.build_context_refs(
                    analysis_id=analysis_id, q=q, contexts=contexts, parent_analysis_id=parent_id,
                ),
            }
            _persist_finish(analysis_id, early)
            return early

        for g in groups:
            yield ask_protocol.step_event(
                "source", "running",
                analysis_id=analysis_id,
                message=f"{g['source']} · {g['issue']}",
                group_id=g["group_id"],
                source=g["source"],
                issue=g["issue"],
            )

        source_reports = []
        with ThreadPoolExecutor(max_workers=min(_SOURCE_PARALLEL, len(groups))) as pool:
            futs = {pool.submit(_analyze_one_source, q, g): g for g in groups}
            for fut in as_completed(futs):
                g = futs[fut]
                usage["llm_calls"] += 1
                try:
                    report = fut.result(timeout=_SOURCE_TIMEOUT + 5)
                except FuturesTimeout:
                    report = {
                        "group_id": g["group_id"], "source": g["source"], "issue": g["issue"],
                        "facts": [], "entities": [], "events": [], "evidence": [],
                        "_fallback": True, "_error": "timeout",
                    }
                except Exception as e:
                    report = {
                        "group_id": g["group_id"], "source": g["source"], "issue": g["issue"],
                        "facts": [], "entities": [], "events": [], "evidence": [],
                        "_error": str(e)[:120],
                    }
                if "group_id" not in report:
                    report["group_id"] = g["group_id"]
                    report["source"] = g["source"]
                    report["issue"] = g["issue"]
                source_reports.append(report)
                st = ask_protocol.source_finish_status(report)
                yield ask_protocol.step_event(
                    "source", st,
                    analysis_id=analysis_id,
                    message=g["source"],
                    group_id=report.get("group_id"),
                    source=report.get("source"),
                    issue=report.get("issue"),
                    facts=report.get("facts") or [],
                    entities=report.get("entities") or [],
                    events=report.get("events") or [],
                    evidence=report.get("evidence") or [],
                )
        order = {g["group_id"]: i for i, g in enumerate(groups)}
        source_reports.sort(key=lambda r: order.get(r.get("group_id"), 99))

    if not source_reports:
        ans = prepared.get("direct_answer") or "可用记录中未找到相关材料。"
        yield ask_protocol.step_event(
            "verify", "completed",
            analysis_id=analysis_id,
            message="无有效来源",
            verified=0, rejected=0, downgraded=0,
        )
        usage["elapsed_ms"] = int((time.time() - t0) * 1000)
        for k, v in _llm_mod.take_usage_accum().items():
            usage[k] = v
        early = {
            "answer": ans, "analysis_id": analysis_id, "report_id": report_id, "usage": usage,
            "verify": {"verified": 0, "rejected": 0, "downgraded": 0},
            "cross": {}, "sources": [],
            "context_refs": ask_context.build_context_refs(
                analysis_id=analysis_id, q=q, contexts=contexts, parent_analysis_id=parent_id,
            ),
        }
        _persist_finish(analysis_id, early)
        return early

    do_cross = ask_context.needs_cross(q, source_reports, mode=prepared.get("mode") or "")
    if do_cross:
        usage["cross"] = True
        if structured:
            usage["path"] = "structured+cross"
        yield ask_protocol.step_event(
            "cross", "running", analysis_id=analysis_id, message="交叉分析中",
        )
        cross = _cross_analyze(q, source_reports)
        if not cross.get("_fallback"):
            usage["llm_calls"] += 1
        cst = ask_protocol.cross_finish_status(cross)
        yield ask_protocol.step_event(
            "cross", cst,
            analysis_id=analysis_id,
            message="交叉完成",
            summary=(cross.get("summary") or "")[:500],
            same_entities=cross.get("same_entities") or [],
            same_events=cross.get("same_events") or [],
            corroborations=cross.get("corroborations") or [],
            conflicts=cross.get("conflicts") or [],
            changes=cross.get("changes") or [],
            claims_n=len(cross.get("claims") or []),
        )
    else:
        cross = _heuristic_cross(q, source_reports)
        yield ask_protocol.step_event(
            "cross", "skipped",
            analysis_id=analysis_id,
            message="无需交叉",
            claims_n=len(cross.get("claims") or []),
        )

    yield ask_protocol.step_event(
        "verify", "running", analysis_id=analysis_id, message="校验中",
    )
    ans, vstats = _verify_and_compose(q, cross, source_reports, contexts)
    usage["llm_calls"] += 1
    yield ask_protocol.step_event(
        "verify", "completed",
        analysis_id=analysis_id,
        message="校验完成",
        verified=vstats["verified"],
        rejected=vstats["rejected"],
        downgraded=vstats["downgraded"],
        flags=vstats.get("flags") or [],
    )

    usage["elapsed_ms"] = int((time.time() - t0) * 1000)
    tok = _llm_mod.take_usage_accum()
    for k, v in tok.items():
        usage[k] = v
    if tok.get("n_calls") is not None:
        # prefer measured call count from provider path
        usage["llm_calls"] = max(int(usage.get("llm_calls") or 0), int(tok["n_calls"]))
    sources_out = [
        {
            "group_id": r.get("group_id"),
            "source": r.get("source"),
            "issue": r.get("issue"),
            "n_facts": len(r.get("facts") or []),
            "evidence": [
                {"ref": ev.get("ref"), "quote": (ev.get("quote") or "")[:200]}
                for ev in (r.get("evidence") or [])[:24]
                if isinstance(ev, dict)
            ],
        }
        for r in source_reports
    ]
    cross_out = {
        "summary": cross.get("summary"),
        "conflicts": cross.get("conflicts"),
        "corroborations": cross.get("corroborations"),
        "same_entities": cross.get("same_entities"),
        "skipped": not do_cross,
    }
    refs = ask_context.build_context_refs(
        analysis_id=analysis_id,
        q=q,
        contexts=contexts,
        sources=sources_out,
        cross=cross_out,
        parent_analysis_id=parent_id,
    )
    result = {
        "answer": ans,
        "analysis_id": analysis_id,
        "report_id": report_id,
        "usage": usage,
        "verify": vstats,
        "context_route": {
            "kind": kind,
            "reason": ctx_route.get("reason"),
            "parent_analysis_id": parent_id,
        },
        "context_refs": refs,
        "cross": cross_out,
        "sources": sources_out,
    }
    _persist_finish(analysis_id, result)
    return result


def run_analysis_collect(
    q: str,
    prepared: dict,
    history: list | None = None,
    *,
    persist: dict | None = None,
) -> tuple[str, list[dict], dict]:
    steps: list[dict] = []
    gen = run_analysis(q, prepared, history=history, persist=persist)
    try:
        while True:
            steps.append(next(gen))
    except StopIteration as e:
        meta = e.value if isinstance(e.value, dict) else {"answer": e.value or ""}
        return (meta.get("answer") or ""), steps, meta
