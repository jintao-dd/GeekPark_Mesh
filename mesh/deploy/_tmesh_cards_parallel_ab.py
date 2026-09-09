#!/usr/bin/env python3
"""Cards parallel A/B on tmesh (preview-only · no Publish · no extract change).

同一期强制重建要点卡：
  A: MESH_PREVIEW_CARD_CONCURRENCY=1（串行）
  B: MESH_PREVIEW_CARD_CONCURRENCY=2

比较 wall / cards wall / LLM sum / call count / retry / 最终 cards 输出差异。

用法（容器内）：
  PYTHONPATH=/srv/mesh MESH_JOB_INLINE=1 \\
    python deploy/_tmesh_cards_parallel_ab.py 2026-09-08
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ["MESH_JOB_INLINE"] = "1"
os.environ.setdefault("MESH_ALLOW_PROD_PUBLISH", "0")
os.environ["MESH_PREVIEW_CARD_FORCE"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, llm, preview_job  # noqa: E402


class LlmProbe:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self._inflight = 0
        self._orig = None

    def install(self) -> None:
        self._orig = llm.call

        def wrapped(system, user, max_tokens=4000, json_mode=True, *, task="default"):
            with self.lock:
                self._inflight += 1
                depth = self._inflight
                idx = len(self.events)
            t0 = time.perf_counter()
            err = ""
            try:
                return self._orig(
                    system, user, max_tokens=max_tokens, json_mode=json_mode, task=task
                )
            except Exception as e:
                err = f"{type(e).__name__}:{e}"
                raise
            finally:
                ms = (time.perf_counter() - t0) * 1000.0
                sys_n = len(system or "") if isinstance(system, str) else 0
                usr_n = len(user or "") if isinstance(user, str) else 0
                with self.lock:
                    self._inflight -= 1
                    self.events.append(
                        {
                            "i": idx,
                            "task": task,
                            "ms": round(ms, 1),
                            "inflight_at_start": depth,
                            "parallel_overlap": depth > 1,
                            "system_chars": sys_n,
                            "user_chars": usr_n,
                            "approx_input_tokens": (sys_n + usr_n) // 2,
                            "max_tokens": max_tokens,
                            "error": err,
                            "wall_ts": time.time(),
                        }
                    )

        llm.call = wrapped  # type: ignore

    def uninstall(self) -> None:
        if self._orig is not None:
            llm.call = self._orig  # type: ignore

    def reset(self) -> None:
        with self.lock:
            self.events = []
            self._inflight = 0


def _issue_meta(slug: str) -> dict:
    con = db.connect()
    try:
        r = con.execute(
            """SELECT id, slug, period_label, status,
                      (SELECT COUNT(*) FROM sources s WHERE s.issue_id=issues.id AND length(COALESCE(s.text,''))>0) AS n_sources,
                      (SELECT COUNT(*) FROM items i WHERE i.issue_id=issues.id) AS n_items,
                      (SELECT COUNT(*) FROM items i WHERE i.issue_id=issues.id AND blocked=0 AND merged_into IS NULL) AS n_active_items
               FROM issues WHERE slug=?""",
            (slug,),
        ).fetchone()
        return dict(r) if r else {}
    finally:
        con.close()


def _clear_cards(issue_id: int) -> int:
    with db.write_lock():
        con = db.connect()
        try:
            n = con.execute("SELECT COUNT(*) AS c FROM cards WHERE issue_id=?", (issue_id,)).fetchone()["c"]
            con.execute("DELETE FROM cards WHERE issue_id=?", (issue_id,))
            con.commit()
            return int(n or 0)
        finally:
            con.close()


def _dump_cards(issue_id: int) -> list[dict]:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT team, card_json, status FROM cards WHERE issue_id=? ORDER BY team",
            (issue_id,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                card = json.loads(r["card_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                card = {"_raw": r["card_json"]}
            # 比较内容时去掉指纹噪声字段若存在
            if isinstance(card, dict):
                card = dict(card)
                card.pop("_items_fp", None)
                card.pop("_fingerprint", None)
            out.append({"team": r["team"], "status": r["status"], "card": card})
        return out
    finally:
        con.close()


def _cards_digest(cards: list[dict]) -> str:
    payload = json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _wait_preview(slug: str, t0: float, phase_log: list, deadline_s: float = 90 * 60) -> dict:
    last_phase = None
    last_msg = None
    deadline = t0 + deadline_s
    while time.time() < deadline:
        st = preview_job.get_state(slug)
        phase = st.get("phase") or ""
        msg = st.get("message") or ""
        if phase != last_phase or msg != last_msg:
            entry = {
                "t_s": round(time.time() - t0, 2),
                "phase": phase,
                "message": msg,
                "cur": st.get("cur"),
                "total": st.get("total"),
                "cards_done": st.get("cards_done"),
                "card_concurrency": st.get("card_concurrency"),
                "running": st.get("running"),
                "done": st.get("done"),
                "error": st.get("error"),
            }
            phase_log.append(entry)
            print(f"PHASE {entry}", flush=True)
            last_phase, last_msg = phase, msg
        if st.get("done"):
            return st
        if st.get("error") and not st.get("running"):
            return st
        time.sleep(1.5)
    raise SystemExit(f"TIMEOUT waiting preview {slug}")


def _cards_wall_from_profile(profile: list[dict] | None) -> float:
    """从 card_start/end 估算 cards 阶段墙钟（并行取 max 覆盖区间）。"""
    if not profile:
        return 0.0
    starts: list[float] = []
    ends: list[float] = []
    for p in profile:
        if p.get("card_status") == "reuse":
            continue
        try:
            s = datetime.fromisoformat(str(p["card_start"]).replace("Z", "+00:00")).timestamp()
            e = datetime.fromisoformat(str(p["card_end"]).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        starts.append(s)
        ends.append(e)
    if not starts:
        return 0.0
    return round(max(ends) - min(starts), 3)


def _run_arm(slug: str, *, conc: int, label: str, probe: LlmProbe) -> dict:
    os.environ["MESH_PREVIEW_CARD_CONCURRENCY"] = str(conc)
    os.environ["MESH_PREVIEW_CARD_FORCE"] = "1"
    # ask_concurrency 在 import 时读 env；cards 用 preview_job._card_concurrency 每次读 env，OK
    meta = _issue_meta(slug)
    issue_id = int(meta["id"])
    deleted = _clear_cards(issue_id)
    print(f"==> {label} concurrency={conc} cleared_cards={deleted}", flush=True)

    probe.reset()
    llm.reset_usage_accum()
    phase_log: list[dict] = []
    t0 = time.time()
    st0 = preview_job.start(slug, f"cards-ab-{label}", force=True)
    print(f"start running={st0.get('running')} err={st0.get('error')!r}", flush=True)
    st = _wait_preview(slug, t0, phase_log)
    wall = round(time.time() - t0, 2)
    usage = llm.take_usage_accum()
    profile = list(st.get("card_profile") or [])
    cards = _dump_cards(issue_id)
    card_events = [e for e in probe.events if "card" in str(e.get("task") or "").lower() or e.get("task") in ("team_card", "build_team_card", "card")]
    # 若 task 名不含 card，用 phase 窗口近似：整次 preview 的 LLM（含 draft）
    all_events = list(probe.events)
    resilience = st.get("resilience") or {}

    # cards wall：优先 profile 覆盖；并报 phase_log 里 cards→draft 的间隔
    cards_wall_profile = _cards_wall_from_profile(profile)
    cards_phase_s = None
    t_cards = next((e["t_s"] for e in phase_log if e.get("phase") == "cards"), None)
    t_after = next(
        (e["t_s"] for e in phase_log if e.get("phase") in ("draft", "relations", "done") and e["t_s"] > (t_cards or 0)),
        None,
    )
    if t_cards is not None and t_after is not None:
        cards_phase_s = round(float(t_after) - float(t_cards), 2)

    return {
        "label": label,
        "concurrency": conc,
        "wall_s": wall,
        "cards_wall_profile_s": cards_wall_profile,
        "cards_phase_s": cards_phase_s,
        "card_profile": profile,
        "card_count": st.get("card_count") or st.get("cards_total"),
        "cards_done": st.get("cards_done"),
        "final_status": st.get("final_status"),
        "error": st.get("error"),
        "resilience": resilience,
        "retry_total": resilience.get("retry_total"),
        "skipped": resilience.get("skipped"),
        "llm": {
            "n_calls": len(all_events),
            "sum_ms": round(sum(float(e["ms"]) for e in all_events), 1),
            "parallel_overlap_n": sum(1 for e in all_events if e.get("parallel_overlap")),
            "by_task": _by_task(all_events),
            "events": all_events,
            "card_like_n": len(card_events),
        },
        "usage_accum": usage,
        "cards_digest": _cards_digest(cards),
        "cards": cards,
        "phase_log": phase_log,
        "preview_url": st.get("preview_url"),
    }


def _by_task(events: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for e in events:
        t = str(e.get("task") or "default")
        bucket = out.setdefault(t, {"n": 0, "sum_ms": 0.0, "overlap_n": 0})
        bucket["n"] += 1
        bucket["sum_ms"] = round(bucket["sum_ms"] + float(e["ms"]), 1)
        if e.get("parallel_overlap"):
            bucket["overlap_n"] += 1
    return out


def _diff_cards(a: list[dict], b: list[dict]) -> dict:
    by_a = {x["team"]: x["card"] for x in a}
    by_b = {x["team"]: x["card"] for x in b}
    teams = sorted(set(by_a) | set(by_b))
    same = []
    different = []
    only_a = []
    only_b = []
    for t in teams:
        if t not in by_a:
            only_b.append(t)
            continue
        if t not in by_b:
            only_a.append(t)
            continue
        ja = json.dumps(by_a[t], ensure_ascii=False, sort_keys=True)
        jb = json.dumps(by_b[t], ensure_ascii=False, sort_keys=True)
        if ja == jb:
            same.append(t)
        else:
            different.append(t)
    return {
        "teams_same": same,
        "teams_different": different,
        "only_a": only_a,
        "only_b": only_b,
        "identical": not different and not only_a and not only_b,
    }


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-08"
    meta = _issue_meta(slug)
    if not meta:
        raise SystemExit(f"missing issue {slug}")
    print(
        f"==> CARDS PARALLEL A/B slug={slug} sources={meta.get('n_sources')} "
        f"items={meta.get('n_items')} active={meta.get('n_active_items')}",
        flush=True,
    )

    probe = LlmProbe()
    probe.install()
    try:
        arm_a = _run_arm(slug, conc=1, label="A_serial", probe=probe)
        arm_b = _run_arm(slug, conc=2, label="B_conc2", probe=probe)
    finally:
        probe.uninstall()

    diff = _diff_cards(arm_a["cards"], arm_b["cards"])
    speedup = None
    if arm_a["cards_phase_s"] and arm_b["cards_phase_s"] and arm_b["cards_phase_s"] > 0:
        speedup = round(arm_a["cards_phase_s"] / arm_b["cards_phase_s"], 3)

    report = {
        "baseline_kind": "cards_parallel_ab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": "tmesh",
        "constraints": {
            "no_publish": True,
            "no_prod_touch": True,
            "preview_only": True,
            "no_extract_change": True,
            "MESH_JOB_INLINE": "1",
            "MESH_PREVIEW_CARD_FORCE": "1",
        },
        "slug": slug,
        "issue": meta,
        "A": {k: v for k, v in arm_a.items() if k != "cards"},
        "B": {k: v for k, v in arm_b.items() if k != "cards"},
        "comparison": {
            "total_wall_s": {"A": arm_a["wall_s"], "B": arm_b["wall_s"], "delta_s": round(arm_b["wall_s"] - arm_a["wall_s"], 2)},
            "cards_phase_s": {"A": arm_a["cards_phase_s"], "B": arm_b["cards_phase_s"], "speedup_A_over_B": speedup},
            "cards_wall_profile_s": {"A": arm_a["cards_wall_profile_s"], "B": arm_b["cards_wall_profile_s"]},
            "llm_sum_ms": {"A": arm_a["llm"]["sum_ms"], "B": arm_b["llm"]["sum_ms"]},
            "llm_call_count": {"A": arm_a["llm"]["n_calls"], "B": arm_b["llm"]["n_calls"]},
            "llm_parallel_overlap_n": {"A": arm_a["llm"]["parallel_overlap_n"], "B": arm_b["llm"]["parallel_overlap_n"]},
            "retry_total": {"A": arm_a["retry_total"], "B": arm_b["retry_total"]},
            "skipped": {"A": arm_a["skipped"], "B": arm_b["skipped"]},
            "cards_digest": {"A": arm_a["cards_digest"], "B": arm_b["cards_digest"]},
            "output_diff": diff,
        },
        "cards_A": arm_a["cards"],
        "cards_B": arm_b["cards"],
    }

    out_dir = ROOT / "eval" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"CARDS_PARALLEL_AB.{slug}.{stamp}.json"
    latest = out_dir / f"CARDS_PARALLEL_AB.{slug}.latest.json"
    md_path = out_dir / f"CARDS_PARALLEL_AB.{slug}.latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    c = report["comparison"]
    md = f"""# Cards Parallel A/B — `{slug}`

Generated: {report["generated_at"]}

## Setup
- preview-only · INLINE=1 · FORCE card rebuild · **no Publish** · extract unchanged
- sources={meta.get("n_sources")} items={meta.get("n_items")} active={meta.get("n_active_items")}
- A: concurrency=1 · B: concurrency=2

## Comparison

| metric | A serial | B conc=2 | note |
|---|---:|---:|---|
| total wall (s) | {c["total_wall_s"]["A"]} | {c["total_wall_s"]["B"]} | delta {c["total_wall_s"]["delta_s"]} |
| cards phase (s) | {c["cards_phase_s"]["A"]} | {c["cards_phase_s"]["B"]} | speedup {c["cards_phase_s"]["speedup_A_over_B"]} |
| cards wall profile (s) | {c["cards_wall_profile_s"]["A"]} | {c["cards_wall_profile_s"]["B"]} | from card_start/end |
| LLM latency sum (ms) | {c["llm_sum_ms"]["A"]} | {c["llm_sum_ms"]["B"]} | |
| LLM call count | {c["llm_call_count"]["A"]} | {c["llm_call_count"]["B"]} | |
| LLM parallel overlap n | {c["llm_parallel_overlap_n"]["A"]} | {c["llm_parallel_overlap_n"]["B"]} | |
| retry_total | {c["retry_total"]["A"]} | {c["retry_total"]["B"]} | |
| cards digest | `{c["cards_digest"]["A"]}` | `{c["cards_digest"]["B"]}` | |
| output identical | {diff["identical"]} | | different teams: {diff["teams_different"]} |

## Per-card profile (A)
{json.dumps(arm_a.get("card_profile"), ensure_ascii=False, indent=2)}

## Per-card profile (B)
{json.dumps(arm_b.get("card_profile"), ensure_ascii=False, indent=2)}

## Raw
- `{json_path.name}`
- `{latest.name}`
"""
    md_path.write_text(md, encoding="utf-8")
    print(json.dumps(c, ensure_ascii=False, indent=2), flush=True)
    print(f"WROTE {json_path}", flush=True)
    print(f"WROTE {md_path}", flush=True)
    return 0 if not arm_a.get("error") and not arm_b.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
