#!/usr/bin/env python3
"""Fetch relation audit for any slug."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "reports"
HOST = "104.250.53.182"
PORT = "22341"
SLUG = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"


def _ssh(cmd: str) -> str:
    full = (
        f'ssh -p {PORT} -o StrictHostKeyChecking=no root@{HOST} '
        f'"docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python {cmd}"'
    )
    p = subprocess.run(full, shell=True, capture_output=True)
    raw = p.stdout.decode("utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit(p.stderr.decode("utf-8", errors="replace"))
    lines = raw.splitlines()
    if lines and lines[0].startswith("[mesh]"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rel = json.loads(_ssh(f"deploy/_report_relation_decisions.py {SLUG}"))
    funnel = json.loads(_ssh(f"deploy/_diag_candidate_funnel.py {SLUG}"))
    out_rel = OUT / f"relation_decisions_{SLUG}.json"
    out_fun = OUT / f"candidate_funnel_{SLUG}.json"
    out_rel.write_text(json.dumps(rel, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_fun.write_text(json.dumps(funnel, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "slug": SLUG,
        "outcome_summary": rel.get("outcome_summary"),
        "n_published_relations": rel.get("n_published_relations"),
        "ledger_lines": rel.get("ledger_lines", [])[:20],
        "funnel": funnel.get("candidate_layer"),
        "decision_layer": funnel.get("decision_layer"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
