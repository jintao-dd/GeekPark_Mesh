#!/usr/bin/env python3
"""Mesh + Agent 全量验收 · 本地回归块执行器。

用法（mesh/ 目录）：
  python eval/run_mesh_agent_full_acceptance.py --phase local_regression

只跑可本地自动化的回归子集；perf / 远程容错 / 并发需另补并写入
eval/reports/MESH_AGENT_FULL_ACCEPTANCE.md。
不触发 Preview / Publish / 飞书。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "eval" / "reports" / "MESH_AGENT_FULL_ACCEPTANCE.md"

# (label, pytest args or special)
LOCAL_PYTEST = [
    ("Agent 20/20", ["tests/test_agent_v1_contract.py"]),
    ("Relation Gold", ["tests/test_relation_gold_schema.py", "tests/test_relation_gold_smoke.py"]),
    ("Claim Check", ["tests/test_relation_claim_check.py", "tests/test_relation_claim_gate_boundary.py"]),
    ("T13", ["tests/test_t13_segment_quality.py"]),
    ("Publish boundary", ["tests/test_publish_boundary.py", "tests/test_published_write_boundary.py"]),
]


def _run(cmd: list[str], cwd: Path) -> tuple[int, float, str]:
    t0 = time.time()
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    dt = time.time() - t0
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, dt, out


def phase_local_regression(*, skip_ask: bool) -> list[dict]:
    rows: list[dict] = []
    for label, files in LOCAL_PYTEST:
        code, dt, out = _run(
            [sys.executable, "-m", "pytest", *files, "-q", "--tb=line"],
            ROOT,
        )
        summary_line = ""
        for line in reversed(out.strip().splitlines()):
            if "passed" in line.lower() or "failed" in line.lower() or "25/25" in line:
                summary_line = line.strip()[:120]
                break
        rows.append(
            {
                "label": label,
                "ok": code == 0,
                "seconds": round(dt, 1),
                "detail": summary_line or f"exit={code}",
            }
        )
        print(f"[{'PASS' if code == 0 else 'FAIL'}] {label} ({dt:.1f}s)")
        if code != 0:
            print("\n".join(out.strip().splitlines()[-8:]))

    if not skip_ask:
        ask_script = ROOT / "eval" / "run_final_eval.py"
        if ask_script.exists():
            code, dt, out = _run(
                [sys.executable, str(ask_script), "--corpus", "golden"],
                ROOT,
            )
            ok = code == 0 and (
                "25/25" in out or "pass_n=25" in out or "PASS 25" in out
            )
            if not ok and code == 0:
                ok = "fail_n=0" in out or "failures: 0" in out.lower()
            summary_line = ""
            for line in reversed(out.strip().splitlines()):
                if "25/25" in line or "pass" in line.lower() or "fail" in line.lower():
                    summary_line = line.strip()[:120]
                    break
            rows.append(
                {
                    "label": "Ask 25/25 golden",
                    "ok": ok,
                    "seconds": round(dt, 1),
                    "detail": summary_line or f"exit={code}",
                }
            )
            print(f"[{'PASS' if ok else 'FAIL'}] Ask 25/25 golden ({dt:.1f}s)")
            if not ok:
                print("\n".join(out.strip().splitlines()[-15:]))
        else:
            rows.append(
                {
                    "label": "Ask 25/25 golden",
                    "ok": False,
                    "seconds": 0,
                    "detail": "run_final_eval.py missing",
                }
            )
    return rows


def _patch_report_regression(rows: list[dict]) -> None:
    if not REPORT.exists():
        return
    text = REPORT.read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    mapping = {
        "Agent 20/20": "Agent 20/20",
        "Ask 25/25 golden": "Ask 25/25 golden",
        "Relation Gold": "Relation Gold",
        "Claim Check": "Claim Check tests",
        "T13": "T13",
        "Publish boundary": "Publish / write boundary",
    }
    for row in rows:
        key = mapping.get(row["label"])
        if not key:
            continue
        status = f"{'PASS' if row['ok'] else 'FAIL'} ({row['seconds']}s @ {stamp})"
        # replace _填_ cells in matching row — simple line rewrite
        lines = []
        for line in text.splitlines():
            if line.startswith(f"| {key} |"):
                lines.append(f"| {key} | {status} | {stamp} | {row['detail'][:80]} |")
            else:
                lines.append(line)
        text = "\n".join(lines) + "\n"
    REPORT.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--phase",
        choices=("local_regression",),
        default="local_regression",
    )
    ap.add_argument("--skip-ask", action="store_true", help="跳过 Ask 25（较慢）")
    args = ap.parse_args()

    if args.phase == "local_regression":
        rows = phase_local_regression(skip_ask=args.skip_ask)
        _patch_report_regression(rows)
        failed = [r for r in rows if not r["ok"]]
        print("\n== summary ==")
        for r in rows:
            print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['label']}")
        print(f"report: {REPORT}")
        return 1 if failed else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
