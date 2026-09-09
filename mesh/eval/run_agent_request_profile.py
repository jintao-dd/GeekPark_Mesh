#!/usr/bin/env python3
"""Agent Performance Sprint · 单请求分段拆速（不改大脑逻辑）。

目标：回答「100s+ 到底死在哪」：
  retrieval / ranking(prepare) / embed / claim gate / semantic LLM×N / answer LLM / total

用法（tmesh 容器内）：
  MESH_AGENT_USE_LLM=1 python eval/run_agent_request_profile.py --reuse-env-db
  python eval/run_agent_request_profile.py --reuse-env-db --q 'XX是否已经量产？'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISSUE = "2026-8-17"
OPEN_ID = "ou_agent_capacity"
Q_SOFT = "编辑部关注了哪些话题或公司"  # capacity 同款
Q_STRONG = "资料能否证明它已经量产？"  # 应进 semantic gate


class CallProbe:
    """串行调用探针：记录每一次外部 LLM / embed 调用。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self._depth = 0

    def record(self, kind: str, **extra: Any) -> Callable:
        def deco(fn: Callable) -> Callable:
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                with self.lock:
                    self._depth += 1
                    depth = self._depth
                    start_idx = len(self.events)
                t0 = time.perf_counter()
                err = ""
                out = None
                try:
                    out = fn(*args, **kwargs)
                    return out
                except Exception as e:
                    err = f"{type(e).__name__}:{e}"
                    raise
                finally:
                    ms = (time.perf_counter() - t0) * 1000.0
                    with self.lock:
                        self._depth -= 1
                        overlap = self._depth > 0  # nested = not pure serial leaf... 
                        # concurrent would need threads; we mark serial if depth was 1 at start
                        self.events.append(
                            {
                                "i": start_idx,
                                "kind": kind,
                                "ms": round(ms, 1),
                                "depth_at_start": depth,
                                "serial_leaf": depth == 1,
                                "error": err,
                                **{k: v for k, v in extra.items() if v is not None},
                            }
                        )
            return wrapped
        return deco

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("kind") == kind]


def _ms(fn: Callable, *a: Any, **kw: Any) -> tuple[Any, float]:
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return out, (time.perf_counter() - t0) * 1000.0


def _install_probes(probe: CallProbe) -> dict[str, Any]:
    from app import llm, embeddings

    info: dict[str, Any] = {
        "embed_enabled": embeddings.enabled(),
        "embed_configured": embeddings.is_configured(),
        "embed_model": embeddings.model_name() if embeddings.is_configured() else None,
        "llm_model_answer": None,
        "llm_model_semantic": None,
        "llm_call_accepts_task": True,
    }
    try:
        info["llm_model_answer"] = llm.model_for_task("answer")
        info["llm_model_semantic"] = llm.model_for_task("semantic")
    except Exception as e:
        info["model_err"] = str(e)

    # 只包一层：避免多次 profile 嵌套 wrap；只钩 embed_texts，避免与 embed_one 双计
    if not getattr(llm, "_mesh_profile_orig_call", None):
        llm._mesh_profile_orig_call = llm.call  # type: ignore
    if not getattr(embeddings, "_mesh_profile_orig_embed_texts", None):
        embeddings._mesh_profile_orig_embed_texts = embeddings.embed_texts  # type: ignore

    orig_call = llm._mesh_profile_orig_call  # type: ignore
    orig_embed_texts = embeddings._mesh_profile_orig_embed_texts  # type: ignore

    import inspect

    try:
        info["llm_call_accepts_task"] = "task" in inspect.signature(orig_call).parameters
    except Exception:
        pass

    def call_wrap(system, user, max_tokens=4000, json_mode=True, *, task="default", **kw):
        t0 = time.perf_counter()
        err = ""
        try:
            if info["llm_call_accepts_task"]:
                return orig_call(
                    system, user, max_tokens=max_tokens, json_mode=json_mode, task=task, **kw
                )
            # 旧 llm.call 无 task=：降级，避免 TypeError 吞掉真实耗时
            return orig_call(system, user, max_tokens=max_tokens, json_mode=json_mode)
        except Exception as e:
            err = f"{type(e).__name__}:{e}"
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            probe.events.append(
                {
                    "i": len(probe.events),
                    "kind": "llm_call",
                    "task": task,
                    "json_mode": bool(json_mode),
                    "max_tokens": max_tokens,
                    "system_chars": len(system or ""),
                    "user_chars": len(user or ""),
                    "ms": round(ms, 1),
                    "serial_leaf": True,
                    "error": err,
                    "task_kw_honored": bool(info["llm_call_accepts_task"]),
                }
            )

    def embed_texts_wrap(texts: list):
        t0 = time.perf_counter()
        err = ""
        try:
            return orig_embed_texts(texts)
        except Exception as e:
            err = f"{type(e).__name__}:{e}"
            raise
        finally:
            probe.events.append(
                {
                    "i": len(probe.events),
                    "kind": "embed_texts",
                    "n": len(texts or []),
                    "ms": round((time.perf_counter() - t0) * 1000.0, 1),
                    "serial_leaf": True,
                    "error": err,
                    "last_error": embeddings.last_error() or "",
                }
            )

    llm.call = call_wrap  # type: ignore
    embeddings.embed_texts = embed_texts_wrap  # type: ignore
    return info


def profile_one(con, q: str, *, issue: str, use_llm: bool) -> dict[str, Any]:
    from app.agent import context as ctxmod
    from app.agent import identity as idmod
    from app.agent import intent as intentmod
    from app.agent import permission as permmod
    from app.agent.models import AgentEnvelope
    from app.agent import claim_support as claim_support_mod
    from app.agent.claim_semantic_ext import cheap_semantic_gate
    from app.agent import temporal as temporal_mod
    from app.agent.temporal import TimeSemantics
    from app.agent.adapters import (
        scope_from_agent,
        _evidence_from_contexts,
        _maybe_llm_answer,
        _summary_from_contexts,
    )
    from app import ask_engine
    from app import embeddings
    from app.ask_planner import plan_retrieval
    # retriever / retrieval_query 不再单独跑，避免与 prepare 双计

    os.environ["MESH_AGENT_USE_LLM"] = "1" if use_llm else "0"
    probe = CallProbe()
    cfg = _install_probes(probe)

    stages: dict[str, float] = {}
    meta: dict[str, Any] = {"query": q, "issue": issue, "use_llm": use_llm, "config": cfg}

    env = AgentEnvelope(
        text=q,
        channel="harness",
        feishu_open_id=OPEN_ID,
        explicit_issue=issue,
    )

    identity, stages["identity_ms"] = _ms(idmod.resolve_identity, con, env)
    chat_team, stages["chat_team_ms"] = _ms(ctxmod.chat_team_of, con, env.chat_id)
    permission, stages["permission_ms"] = _ms(
        permmod.decide_permission,
        identity,
        explicit_team=env.explicit_team,
        chat_team=chat_team,
    )
    context, stages["context_ms"] = _ms(
        ctxmod.assemble_context, con, env, identity, permission
    )
    intent, stages["intent_ms"] = _ms(
        intentmod.rule_classify_intent, env.text, context, permission
    )
    meta["intent"] = intent
    meta["identity_status"] = identity.status

    t_tool0 = time.perf_counter()

    # —— mirror ask.published hot path with finer stages ——
    scope, sem_dict = scope_from_agent(identity, permission, context, con=con, query=q)
    sem = TimeSemantics(**{k: sem_dict[k] for k in TimeSemantics.__dataclass_fields__})
    early = temporal_mod.maybe_direct_answer(q, sem)
    if early:
        stages["tool_total_ms"] = (time.perf_counter() - t_tool0) * 1000.0
        return {
            "stages_ms": {k: round(v, 1) for k, v in stages.items()},
            "early_temporal": True,
            "external_calls": probe.events,
            "meta": meta,
        }

    # prepare 一次（内含 plan/embed/retrieve/assemble）；外部调用由 probe 捕获
    prepared, prepare_ms = _ms(ask_engine.prepare, con, q, scope)
    stages["prepare_total_ms"] = prepare_ms
    contexts = list(prepared.get("contexts") or [])
    meta["n_context"] = len(contexts)
    meta["prepare_n_hits"] = prepared.get("n_hits")
    meta["prepare_mode"] = prepared.get("mode")
    meta["prepare_latency_ms"] = prepared.get("latency_ms")

    # prepare 内 embed 已记入 probe；这里再标 plan 信息（便宜）
    plan = plan_retrieval(q, None)
    meta["plan"] = {
        "path": plan.path,
        "set_op": plan.set_op,
        "confidence": plan.confidence,
    }
    meta["embed_configured"] = embeddings.is_configured()
    meta["embed_enabled"] = embeddings.enabled()

    contexts, enrich_ms = _ms(
        claim_support_mod.enrich_contexts_for_denial_counter_evidence,
        con,
        scope,
        q,
        contexts,
    )
    stages["enrich_denial_ms"] = enrich_ms
    evidence = _evidence_from_contexts(contexts, issue)

    gate, gate_ms = _ms(cheap_semantic_gate, q)
    stages["semantic_gate_ms"] = gate_ms
    meta["semantic_gate"] = gate

    # claim_support 内可能串行 1 次 semantic llm.call
    n_llm_before = len(probe.by_kind("llm_call"))
    try:
        from app import llm as llm_mod

        llm_mod.reset_usage_accum()
    except Exception:
        pass
    support, claim_ms = _ms(
        claim_support_mod.assess_claim_support,
        q,
        contexts=contexts,
        evidence_refs=evidence,
    )
    try:
        from app import llm as llm_mod

        meta["claim_usage"] = llm_mod.take_usage_accum()
    except Exception:
        pass
    stages["claim_support_ms"] = claim_ms
    meta["claim_support"] = {
        "support": support.get("support"),
        "reason": support.get("reason"),
        "semantic_path": (support.get("semantic") or {}).get("path"),
        "semantic_enabled": (support.get("semantic") or {}).get("enabled"),
    }
    meta["llm_calls_during_claim"] = len(probe.by_kind("llm_call")) - n_llm_before

    abstain = claim_support_mod.abstain_answer_for_unsupported_claim(support)
    answer_ms = 0.0
    llm_used = False
    answer = ""
    if abstain:
        answer = abstain
        meta["abstained"] = True
    else:
        tblock = temporal_mod.prompt_block(sem)
        answer = (prepared.get("direct_answer") or "").strip()
        if not answer and contexts:
            # Answer 上下文预算 profile（不改成文语义）
            try:
                from app import llm as llm_mod

                packed = llm_mod.pack_answer_contexts(contexts)
                sys_p, user_p = llm_mod._qa_prompt(q, contexts, "lexical", temporal_block=tblock)
                meta["answer_prompt_profile"] = {
                    "n_contexts_sent": len(packed),
                    "max_tokens": llm_mod.answer_max_tokens(),
                    "system_chars": len(sys_p or ""),
                    "user_chars": len(user_p or ""),
                    "approx_input_tokens": (len(sys_p or "") + len(user_p or "")) // 2,
                }
            except Exception as e:
                meta["answer_prompt_profile"] = {"error": str(e)}
            n_before = len(probe.by_kind("llm_call"))
            try:
                from app import llm as llm_mod

                llm_mod.reset_usage_accum()
            except Exception:
                pass
            llm_ans, answer_ms = _ms(
                _maybe_llm_answer, q, contexts, temporal_block=tblock
            )
            try:
                from app import llm as llm_mod

                meta["answer_usage"] = llm_mod.take_usage_accum()
            except Exception:
                pass
            meta["llm_calls_during_answer"] = len(probe.by_kind("llm_call")) - n_before
            if llm_ans:
                answer = llm_ans
                llm_used = True
                meta["answer_completion_chars"] = len(answer)
            else:
                answer = _summary_from_contexts(contexts, q)
        meta["abstained"] = False
    stages["answer_llm_wall_ms"] = answer_ms
    meta["llm_used"] = llm_used
    meta["answer_head"] = (answer or "")[:160]

    stages["tool_total_ms"] = (time.perf_counter() - t_tool0) * 1000.0

    llm_calls = probe.by_kind("llm_call")
    sem_calls = [c for c in llm_calls if c.get("task") == "semantic"]
    ans_calls = [c for c in llm_calls if c.get("task") == "answer"]
    other_llm = [c for c in llm_calls if c.get("task") not in ("semantic", "answer")]
    embed_calls = [e for e in probe.events if e["kind"] in ("embed_one", "embed_texts")]

    # 同线程依次 append → 串行；若未来并行会看到重叠时间戳（此处先看 count+order）
    serial = True
    if len(llm_calls) >= 2:
        # 若后一次开始前前一次已结束（我们只记整段 wall），顺序记录即串行
        serial = all(c.get("serial_leaf", True) for c in llm_calls)

    # prepare 内非 embed 近似：prepare - embed_sum（下限 0）
    embed_sum = sum(e["ms"] for e in embed_calls)
    retrieve_approx = max(0.0, prepare_ms - embed_sum)

    summary = {
        "total_profiled_tool_ms": round(stages["tool_total_ms"], 1),
        "embed_call_count": len(embed_calls),
        "embed_ms_sum": round(embed_sum, 1),
        "retrieve_approx_ms": round(retrieve_approx, 1),
        "semantic_call_count": len(sem_calls),
        "semantic_ms_each": [c["ms"] for c in sem_calls],
        "semantic_ms_sum": round(sum(c["ms"] for c in sem_calls), 1),
        "answer_call_count": len(ans_calls),
        "answer_ms_each": [c["ms"] for c in ans_calls],
        "answer_ms_sum": round(sum(c["ms"] for c in ans_calls), 1),
        "other_llm_call_count": len(other_llm),
        "other_llm_ms_each": [c["ms"] for c in other_llm],
        "llm_calls_serial": serial,
        "llm_call_count_total": len(llm_calls),
        "dominant": None,
        "buckets_ms": {},
    }
    buckets = {
        "embed": summary["embed_ms_sum"],
        "prepare_minus_embed≈retrieve+rank+assemble": summary["retrieve_approx_ms"],
        "enrich_denial": stages.get("enrich_denial_ms", 0),
        "claim_support_wall": stages.get("claim_support_ms", 0),
        "semantic_llm": summary["semantic_ms_sum"],
        "answer_llm": summary["answer_ms_sum"] or stages.get("answer_llm_wall_ms", 0),
    }
    summary["buckets_ms"] = {k: round(float(v), 1) for k, v in buckets.items()}
    summary["dominant"] = max(buckets.items(), key=lambda x: x[1])[0] if buckets else None

    return {
        "stages_ms": {k: round(float(v), 1) for k, v in stages.items()},
        "summary": summary,
        "external_calls": probe.events,
        "meta": meta,
    }


def _write_md(report: dict[str, Any], path: Path) -> None:
    man = report.get("environment_manifest") or {}
    lines = [
        "# Agent Request Profile · Performance Sprint",
        "",
        f"**when**=`{report.get('timestamp')}`",
        "",
        "## Environment Manifest",
        "",
        f"- commit=`{man.get('commit')}`",
        f"- image=`{man.get('image_digest') or man.get('image_tag')}`",
        f"- model=`{man.get('model')}`",
        f"- vector=`{man.get('vector')}`",
        f"- embedding_calls=`{man.get('embedding_calls')}`",
        f"- ranking=`{man.get('ranking')}`",
        f"- claim_support=`{man.get('claim_support')}`",
        "",
    ]
    for run in report.get("runs") or []:
        q = (run.get("meta") or {}).get("query") or ""
        s = run.get("summary") or {}
        st = run.get("stages_ms") or {}
        lines += [
            f"## Query: {q}",
            "",
            f"- dominant=`{s.get('dominant')}`",
            f"- tool_total=`{s.get('total_profiled_tool_ms')}` ms",
            f"- embed_call_count=`{s.get('embed_call_count')}` sum=`{s.get('embed_ms_sum')}` ms",
            f"- semantic_call_count=`{s.get('semantic_call_count')}` each=`{s.get('semantic_ms_each')}` sum=`{s.get('semantic_ms_sum')}`",
            f"- answer_call_count=`{s.get('answer_call_count')}` each=`{s.get('answer_ms_each')}` sum=`{s.get('answer_ms_sum')}`",
            f"- llm_calls_serial=`{s.get('llm_calls_serial')}` total_llm=`{s.get('llm_call_count_total')}`",
            f"- buckets=`{json.dumps(s.get('buckets_ms'), ensure_ascii=False)}`",
            "",
            "### Stages (ms)",
            "",
            "```",
            json.dumps(st, ensure_ascii=False, indent=2),
            "```",
            "",
            "### External calls",
            "",
            "```",
            json.dumps(run.get("external_calls") or [], ensure_ascii=False, indent=2),
            "```",
            "",
            f"gate=`{json.dumps((run.get('meta') or {}).get('semantic_gate'), ensure_ascii=False)}`",
            f"claim=`{json.dumps((run.get('meta') or {}).get('claim_support'), ensure_ascii=False)}`",
            f"embed_configured=`{(run.get('meta') or {}).get('config')}`",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--issue", default=ISSUE)
    ap.add_argument("--q", action="append", default=[], help="可重复；默认 soft+strong 各一")
    ap.add_argument("--no-llm-answer", action="store_true")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()

    from app import db

    con = db.connect()
    # ensure user
    row = con.execute(
        "SELECT id FROM users WHERE feishu_open_id=?", (OPEN_ID,)
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
            "VALUES ('agent_capacity','Agent Capacity','x','viewer','编辑部',?)",
            (OPEN_ID,),
        )
        con.commit()

    qs = list(args.q) or [Q_SOFT, Q_STRONG]
    use_llm = not args.no_llm_answer
    from app import embeddings
    from app.repro_selfcheck import environment_manifest

    embeddings.reset_call_count()
    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue": args.issue,
        "use_llm_answer": use_llm,
        "runs": [],
    }
    try:
        for q in qs:
            print(f"==> profile q={q!r}", flush=True)
            run = profile_one(con, q, issue=args.issue, use_llm=use_llm)
            report["runs"].append(run)
            s = run["summary"]
            print(
                f"   dominant={s['dominant']} tool={s['total_profiled_tool_ms']}ms "
                f"embed={s['embed_ms_sum']} semantic={s['semantic_ms_sum']} "
                f"answer={s['answer_ms_sum']} calls_llm={s['llm_call_count_total']}",
                flush=True,
            )
    finally:
        con.close()

    embed_total = sum(int((r.get("summary") or {}).get("embed_call_count") or 0) for r in report["runs"])
    # also fold process counter (catches silent calls outside probe)
    embed_total = max(embed_total, embeddings.call_count())
    report["environment_manifest"] = environment_manifest(embedding_calls=embed_total)
    man = report["environment_manifest"]
    if (not man.get("vector_enabled")) and embed_total > 0:
        report["environment_gate"] = "FAIL"
        print(
            f"ENVIRONMENT_GATE_FAIL vector=OFF but embedding_calls={embed_total}",
            flush=True,
        )
    else:
        report["environment_gate"] = "PASS"

    out_json = Path(
        args.out_json
        or str(ROOT / "eval" / "reports" / "AGENT_REQUEST_PROFILE.tmesh.json")
    )
    out_md = Path(
        args.out_md or str(ROOT / "eval" / "reports" / "AGENT_REQUEST_PROFILE.tmesh.md")
    )
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(report, out_md)
    print(f"wrote {out_json}", flush=True)
    print(f"wrote {out_md}", flush=True)
    print(
        f"manifest commit={man.get('commit')} vector={man.get('vector')} "
        f"model={man.get('model')} embedding_calls={embed_total} gate={report['environment_gate']}",
        flush=True,
    )
    return 0 if report["environment_gate"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
