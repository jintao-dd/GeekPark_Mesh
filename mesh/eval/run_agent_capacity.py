#!/usr/bin/env python3
"""Feishu Agent v1 · HTTP Capacity / Pressure Test（不经真实飞书事件）。

压：POST /api/agent/v1/message
并发阶梯：1 / 2 / 4 / 8 / 16 / 32 / 64
产出：C_safe / C_knee / C_max + 每档成功率 / P50·P95·P99 / TTFB / 429·timeout / 质量抽检

用法（建议在 tmesh 容器宿主机或容器内跑）：
  MESH_SECRET=... python eval/run_agent_capacity.py \\
    --base-url http://127.0.0.1:8091 \\
    --levels 1,2,4,8,16,32,64 \\
    --timeout-s 180

环境：
  MESH_CAPACITY_COOKIE   可选，直接注入 mesh_session
  MESH_SECRET            用于伪造 viewer session（默认容器内 change-me 或 .env）
  MESH_AGENT_USE_LLM     由服务端环境决定；本脚本只读响应 llm_used
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISSUE = "2026-8-17"
Q = "编辑部关注了哪些话题或公司"
OPEN_ID = "ou_agent_capacity"


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    if len(ys) == 1:
        return float(ys[0])
    k = (len(ys) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return float(ys[f])
    return float(ys[f] + (ys[c] - ys[f]) * (k - f))


def _summary(xs: list[float]) -> dict[str, Any]:
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean_ms": round(statistics.mean(xs), 1),
        "p50_ms": round(_pct(xs, 50), 1),
        "p95_ms": round(_pct(xs, 95), 1),
        "p99_ms": round(_pct(xs, 99), 1),
        "max_ms": round(max(xs), 1),
        "min_ms": round(min(xs), 1),
    }


def _forge_cookie(secret: str, username: str = "admin", role: str = "owner") -> str:
    from itsdangerous import URLSafeSerializer

    tok = URLSafeSerializer(secret, salt="mesh-session").dumps(
        {
            "u": username,
            "r": role,
            "t": "编辑部",
            "d": username,
            "a": "",
            "ts": int(time.time()),
        }
    )
    return f"mesh_session={tok}"


def _docker_stats(container: str) -> dict[str, Any] | None:
    if not container:
        return None
    import subprocess

    try:
        out = subprocess.check_output(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
                container,
            ],
            text=True,
            timeout=15,
        ).strip()
        parts = out.split("\t")
        if len(parts) >= 3:
            return {
                "cpu_perc": parts[0].strip(),
                "mem_usage": parts[1].strip(),
                "mem_perc": parts[2].strip(),
            }
    except Exception as e:
        return {"error": str(e)}
    return None


def _one_http(
    *,
    base_url: str,
    cookie: str,
    timeout_s: float,
    idx: int,
    concurrency: int,
) -> dict[str, Any]:
    request_id = f"cap{concurrency:02d}_{idx:03d}_{uuid.uuid4().hex[:8]}"
    payload = {
        "text": Q,
        "channel": "harness",
        "feishu_open_id": OPEN_ID,
        "explicit_issue": ISSUE,
        "session_id": f"cap-sess-{concurrency}",
        "request_id": request_id,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}/api/agent/v1/message",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie,
            "X-Request-Id": request_id,
        },
    )
    t_queue = time.perf_counter()  # 提交时刻（近似排队起点）
    # 实际开始读网络前
    t_start = time.perf_counter()
    queue_wait_ms = (t_start - t_queue) * 1000.0
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            t_ttfb = time.perf_counter()
            raw = resp.read()
            t_end = time.perf_counter()
            status = int(resp.status)
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                data = {"_raw": raw[:200].decode("utf-8", "replace")}
            obs = data.get("observability") if isinstance(data, dict) else {}
            text = (data.get("display_text") or data.get("text") or "") if isinstance(data, dict) else ""
            quality_ok = bool(text.strip()) and not (
                isinstance(data, dict) and data.get("refused") and data.get("intent") == "refuse"
                and "绑定" in text
            )
            # 质量：可验证问答应有 evidence 块或 claim 判定（display_text 含期次/证据）
            has_evidence_ux = ("期次" in text) or ("证据" in text) or bool(
                (data.get("evidence_refs") if isinstance(data, dict) else None) or []
            )
            return {
                "ok": 200 <= status < 300 and bool(text.strip()),
                "http_status": status,
                "request_id": request_id,
                "ttfb_ms": round((t_ttfb - t_start) * 1000.0, 1),
                "total_ms": round((t_end - t_start) * 1000.0, 1),
                "queue_wait_ms": round(queue_wait_ms, 1),
                "server_latency_ms": (obs or {}).get("latency_ms") if isinstance(obs, dict) else None,
                "timeout": False,
                "http_429": status == 429,
                "error": "",
                "intent": data.get("intent") if isinstance(data, dict) else None,
                "llm_used": (obs or {}).get("llm_used") if isinstance(obs, dict) else None,
                "answer_status": (obs or {}).get("answer_status") if isinstance(obs, dict) else None,
                "evidence_count": (obs or {}).get("evidence_count") if isinstance(obs, dict) else None,
                "retrieval_n_hits": (obs or {}).get("retrieval_n_hits") if isinstance(obs, dict) else None,
                "model_used": (obs or {}).get("model_used") if isinstance(obs, dict) else None,
                "quality_ok": quality_ok,
                "has_evidence_ux": has_evidence_ux,
                "text_head": text[:120],
                "retries": 0,
            }
    except error.HTTPError as e:
        t_end = time.perf_counter()
        body = ""
        try:
            body = e.read(400).decode("utf-8", "replace")
        except Exception:
            pass
        return {
            "ok": False,
            "http_status": int(e.code),
            "request_id": request_id,
            "ttfb_ms": round((t_end - t_start) * 1000.0, 1),
            "total_ms": round((t_end - t_start) * 1000.0, 1),
            "queue_wait_ms": round(queue_wait_ms, 1),
            "server_latency_ms": None,
            "timeout": False,
            "http_429": int(e.code) == 429,
            "error": f"HTTPError:{e.code}:{body[:160]}",
            "intent": None,
            "llm_used": None,
            "answer_status": "error",
            "evidence_count": None,
            "retrieval_n_hits": None,
            "model_used": None,
            "quality_ok": False,
            "has_evidence_ux": False,
            "text_head": "",
            "retries": 0,
        }
    except Exception as e:
        t_end = time.perf_counter()
        msg = str(e)
        is_to = "timed out" in msg.lower() or "timeout" in msg.lower()
        return {
            "ok": False,
            "http_status": 0,
            "request_id": request_id,
            "ttfb_ms": round((t_end - t_start) * 1000.0, 1),
            "total_ms": round((t_end - t_start) * 1000.0, 1),
            "queue_wait_ms": round(queue_wait_ms, 1),
            "server_latency_ms": None,
            "timeout": is_to,
            "http_429": False,
            "error": msg[:240],
            "intent": None,
            "llm_used": None,
            "answer_status": "error",
            "evidence_count": None,
            "retrieval_n_hits": None,
            "model_used": None,
            "quality_ok": False,
            "has_evidence_ux": False,
            "text_head": "",
            "retries": 0,
        }


def _run_level(
    *,
    base_url: str,
    cookie: str,
    concurrency: int,
    timeout_s: float,
    container: str,
    rounds: int,
) -> dict[str, Any]:
    n = max(1, concurrency * max(1, rounds))
    stats_before = _docker_stats(container)
    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [
            pool.submit(
                _one_http,
                base_url=base_url,
                cookie=cookie,
                timeout_s=timeout_s,
                idx=i,
                concurrency=concurrency,
            )
            for i in range(n)
        ]
        for fut in as_completed(futs):
            results.append(fut.result())
    wall_s = time.perf_counter() - t0
    stats_after = _docker_stats(container)

    ok_n = sum(1 for r in results if r.get("ok"))
    err_n = n - ok_n
    n429 = sum(1 for r in results if r.get("http_429"))
    n_to = sum(1 for r in results if r.get("timeout"))
    totals = [float(r["total_ms"]) for r in results if r.get("total_ms") is not None]
    ttfbs = [float(r["ttfb_ms"]) for r in results if r.get("ttfb_ms") is not None]
    servers = [
        float(r["server_latency_ms"])
        for r in results
        if r.get("server_latency_ms") is not None
    ]
    llm_n = sum(1 for r in results if r.get("llm_used") is True)
    q_ok = sum(1 for r in results if r.get("quality_ok"))
    ev_ux = sum(1 for r in results if r.get("has_evidence_ux"))

    return {
        "concurrency": concurrency,
        "n_requests": n,
        "wall_s": round(wall_s, 2),
        "throughput_rps": round(n / wall_s, 3) if wall_s > 0 else 0.0,
        "success_n": ok_n,
        "error_n": err_n,
        "success_rate": round(ok_n / n, 4) if n else 0.0,
        "error_rate": round(err_n / n, 4) if n else 0.0,
        "http_429_n": n429,
        "timeout_n": n_to,
        "llm_used_n": llm_n,
        "quality_ok_rate": round(q_ok / n, 4) if n else 0.0,
        "evidence_ux_rate": round(ev_ux / n, 4) if n else 0.0,
        "total_ms": _summary(totals),
        "ttfb_ms": _summary(ttfbs),
        "server_latency_ms": _summary(servers),
        "docker_before": stats_before,
        "docker_after": stats_after,
        "errors_sample": [r.get("error") for r in results if r.get("error")][:5],
        "results": results,
    }


def _classify(levels: list[dict[str, Any]]) -> dict[str, Any]:
    """启发式：C_safe / C_knee / C_max。"""
    if not levels:
        return {"C_safe": None, "C_knee": None, "C_max": None, "notes": []}

    base = levels[0]
    base_p95 = float((base.get("total_ms") or {}).get("p95_ms") or 0) or 1.0
    notes: list[str] = []
    c_safe = None
    c_knee = None
    c_max = None

    for lv in levels:
        c = int(lv["concurrency"])
        sr = float(lv.get("success_rate") or 0)
        p95 = float((lv.get("total_ms") or {}).get("p95_ms") or 0)
        er = float(lv.get("error_rate") or 0)
        to_n = int(lv.get("timeout_n") or 0)
        n429 = int(lv.get("http_429_n") or 0)
        q = float(lv.get("quality_ok_rate") or 0)
        ev = float(lv.get("evidence_ux_rate") or 0)

        unstable = (
            sr < 0.99
            or er > 0.01
            or to_n > 0
            or n429 > 0
            or q < 0.95
            or ev < 0.90
            or (p95 > base_p95 * 2.0 and c > 1)
        )
        blowup = sr < 0.90 or er > 0.10 or to_n >= max(1, int(lv["n_requests"] * 0.05)) or n429 > 0

        if not unstable:
            c_safe = c
        elif c_knee is None:
            c_knee = c
            notes.append(
                f"C_knee={c}: success={sr}, p95={p95} (base_p95={base_p95}), "
                f"timeout={to_n}, 429={n429}, quality={q}, evidence_ux={ev}"
            )
        if blowup and c_max is None:
            c_max = c
            notes.append(
                f"C_max={c}: success={sr}, error={er}, timeout={to_n}, 429={n429}"
            )

    if c_safe is None:
        notes.append("未找到满足 success≥99% 且 P95≤2×基线 的档位；C_safe 置空")
    if c_knee is None and c_safe is not None:
        # 全阶梯仍稳定：knee 取最高测得并发之上（未知）
        notes.append("在测试阶梯内未观察到明显恶化；C_knee 未触发")
    if c_max is None:
        notes.append("在测试阶梯内未出现大量 timeout/429/error；C_max 未触发")

    return {
        "C_safe": c_safe,
        "C_knee": c_knee,
        "C_max": c_max,
        "baseline_p95_ms": round(base_p95, 1),
        "notes": notes,
    }


def _write_md(report: dict[str, Any], path: Path) -> None:
    cls = report.get("classification") or {}
    man = report.get("environment_manifest") or {}
    lines = [
        "# Agent Capacity / E2E Pressure Test",
        "",
        f"**target**=`{report.get('base_url')}` · **issue**=`{report.get('issue')}` · "
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
        f"- C_safe = `{cls.get('C_safe')}`",
        f"- C_knee = `{cls.get('C_knee')}`",
        f"- C_max = `{cls.get('C_max')}`",
        f"- baseline P95 = `{cls.get('baseline_p95_ms')}` ms",
        "",
        "## Levels",
        "",
        "| C | n | success | err | 429 | timeout | P50 | P95 | P99 | TTFB P95 | rps | quality | evidence_ux | CPU after |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lv in report.get("levels") or []:
        tot = lv.get("total_ms") or {}
        ttfb = lv.get("ttfb_ms") or {}
        after = lv.get("docker_after") or {}
        lines.append(
            "| {c} | {n} | {sr:.0%} | {er:.0%} | {n429} | {nto} | {p50} | {p95} | {p99} | {tp95} | {rps} | {q:.0%} | {ev:.0%} | {cpu} |".format(
                c=lv.get("concurrency"),
                n=lv.get("n_requests"),
                sr=float(lv.get("success_rate") or 0),
                er=float(lv.get("error_rate") or 0),
                n429=lv.get("http_429_n"),
                nto=lv.get("timeout_n"),
                p50=tot.get("p50_ms"),
                p95=tot.get("p95_ms"),
                p99=tot.get("p99_ms"),
                tp95=ttfb.get("p95_ms"),
                rps=lv.get("throughput_rps"),
                q=float(lv.get("quality_ok_rate") or 0),
                ev=float(lv.get("evidence_ux_rate") or 0),
                cpu=(after.get("cpu_perc") if isinstance(after, dict) else "") or "",
            )
        )
    lines.append("")
    lines.append("## Classification notes")
    lines.append("")
    for n in cls.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("## Heuristics")
    lines.append("")
    lines.append("- **C_safe**: success≥99%, no timeout/429, quality≥95%, evidence_ux≥90%, P95≤2×(C=1)")
    lines.append("- **C_knee**: first level failing C_safe")
    lines.append("- **C_max**: success<90% or error>10% or timeout≥5% or any 429")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("MESH_BASE_URL", "http://127.0.0.1:8091"))
    ap.add_argument("--levels", default="1,2,4,8,16,32,64")
    ap.add_argument("--timeout-s", type=float, default=180.0)
    ap.add_argument("--rounds", type=int, default=1, help="每档请求数 = concurrency * rounds")
    ap.add_argument("--container", default=os.environ.get("MESH_CAPACITY_CONTAINER", "geekpark-tmesh"))
    ap.add_argument("--cookie", default=os.environ.get("MESH_CAPACITY_COOKIE", ""))
    ap.add_argument("--secret", default=os.environ.get("MESH_SECRET", ""))
    ap.add_argument("--username", default="admin")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--pause-s", type=float, default=2.0, help="档位之间冷却")
    args = ap.parse_args()

    levels = [int(x.strip()) for x in args.levels.split(",") if x.strip()]
    cookie = (args.cookie or "").strip()
    if not cookie:
        secret = (args.secret or os.environ.get("MESH_SECRET") or "").strip()
        if not secret:
            # 尝试从常见路径读（容器挂载）
            for p in ("/srv/mesh/.env", "/opt/geekpark-tmesh/.env", str(ROOT / ".env")):
                try:
                    for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.startswith("MESH_SECRET="):
                            secret = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
                except Exception:
                    pass
                if secret:
                    break
        if not secret:
            print("Need --cookie or MESH_SECRET", file=sys.stderr)
            return 2
        cookie = _forge_cookie(secret, username=args.username)

    out_json = Path(
        args.out_json
        or str(ROOT / "eval" / "reports" / "AGENT_CAPACITY_PRESSURE.tmesh.json")
    )
    out_md = Path(
        args.out_md
        or str(ROOT / "eval" / "reports" / "AGENT_CAPACITY_PRESSURE.tmesh.md")
    )

    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "issue": ISSUE,
        "query": Q,
        "levels_requested": levels,
        "timeout_s": args.timeout_s,
        "rounds": args.rounds,
        "container": args.container,
        "note": "HTTP Agent only; Feishu event layer not included. Token accum not reliable under concurrency.",
        "levels": [],
    }
    try:
        from app.repro_selfcheck import environment_manifest

        report["environment_manifest"] = environment_manifest(embedding_calls=None)
        # Prefer live /api/repro/status when available (same env as traffic)
        try:
            # Prefer live /api/repro/status with admin session (endpoint is admin/internal only)
            req = request.Request(
                args.base_url.rstrip("/") + "/api/repro/status",
                method="GET",
                headers={"Cookie": cookie},
            )
            with request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            em = body.get("environment_manifest") or {}
            if em:
                report["environment_manifest"] = em
                report["REPRO_STATUS"] = body.get("REPRO_STATUS")
        except Exception:
            pass
    except Exception as e:
        report["environment_manifest"] = {"error": str(e)}

    print(f"==> capacity base={args.base_url} levels={levels}", flush=True)
    man = report.get("environment_manifest") or {}
    print(
        f"==> manifest commit={man.get('commit')} vector={man.get('vector')} "
        f"model={man.get('model')} image={man.get('image_digest') or man.get('image_tag')}",
        flush=True,
    )
    for c in levels:
        print(f"-- level C={c} ...", flush=True)
        lv = _run_level(
            base_url=args.base_url,
            cookie=cookie,
            concurrency=c,
            timeout_s=args.timeout_s,
            container=args.container,
            rounds=args.rounds,
        )
        # 报告里保留明细但可截断超大
        slim = dict(lv)
        if len(slim.get("results") or []) > 32:
            slim["results"] = (slim["results"] or [])[:8] + (slim["results"] or [])[-4:]
            slim["results_truncated"] = True
        report["levels"].append(slim)
        tot = lv.get("total_ms") or {}
        print(
            f"   success={lv['success_rate']:.0%} p95={tot.get('p95_ms')} "
            f"429={lv['http_429_n']} timeout={lv['timeout_n']} rps={lv['throughput_rps']}",
            flush=True,
        )
        if args.pause_s > 0:
            time.sleep(args.pause_s)

    report["classification"] = _classify(report["levels"])
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(report, out_md)
    print(json.dumps(report["classification"], ensure_ascii=False, indent=2), flush=True)
    print(f"wrote {out_json}", flush=True)
    print(f"wrote {out_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
