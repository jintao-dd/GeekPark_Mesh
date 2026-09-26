#!/usr/bin/env python3
"""从 tmesh 拉取 UTF-8 报告到本地 eval/reports/。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "reports"
HOST = "104.250.53.182"
PORT = "22341"
SLUG = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"


def _ssh_docker(cmd: str) -> str:
    full = (
        f'ssh -p {PORT} -o StrictHostKeyChecking=no root@{HOST} '
        f'"docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python {cmd}"'
    )
    p = subprocess.run(full, shell=True, capture_output=True)
    raw = p.stdout.decode("utf-8", errors="replace")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace")
        raise SystemExit(f"remote failed ({p.returncode}): {err}")
    return raw


def _strip_mesh_log(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("[mesh]"):
        lines = lines[1:]
    return "\n".join(lines).strip() + "\n"


def _write_json(name: str, payload: object) -> Path:
    path = OUT / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    rel_raw = _strip_mesh_log(_ssh_docker(f"deploy/_report_relation_decisions.py {SLUG}"))
    rel = json.loads(rel_raw)
    p1 = _write_json(f"relation_decisions_{SLUG}.json", rel)
    print(f"wrote {p1}")

    phase_raw = _strip_mesh_log(_ssh_docker(f"deploy/run_phase_b_acceptance.py --slug {SLUG}"))
    phase = json.loads(phase_raw)
    p2 = _write_json(f"phase_b_after_two_phase_{SLUG}.json", phase)
    print(f"wrote {p2}")

    # 兼容旧文件名
    p2b = OUT / "phase_b_after_two_phase.json"
    p2b.write_text(json.dumps(phase, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {p2b}")


if __name__ == "__main__":
    main()
