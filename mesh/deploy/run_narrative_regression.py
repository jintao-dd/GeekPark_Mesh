#!/usr/bin/env python3
"""Narrative apply 后完整回归 + publish gate。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _run_py(script: str, *args: str) -> int:
    r = subprocess.run(
        [sys.executable, str(ROOT / "deploy" / script), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    return r.returncode


def main():
    slug = "2026-8-17"
    print("=== pytest (attribution + narrative + relation) ===")
    tests = [
        "tests/test_narrative_clean.py",
        "tests/test_attribution_verify.py",
        "tests/test_attribution.py",
        "tests/test_relation_classify.py",
        "tests/test_owner_guard.py",
        "tests/test_golden_attribution.py",
    ]
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit(r.returncode)

    print("\n=== Attribution Verify ===")
    if _run_py("run_attribution_verify.py", "--slug", slug) != 0:
        sys.exit(1)

    print("\n=== Relation audit ===")
    _run_py("classify_relation_blockers.py", "--slug", slug, "--audit-all")

    print("\n=== Publish gate ===")
    from app import db
    from app.main import publish_blockers

    con = db.connect()
    row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    blockers = publish_blockers(con, row["id"], row["draft_json"] or "")
    con.close()
    print(json.dumps({"publish_blockers": blockers, "count": len(blockers)}, ensure_ascii=False, indent=2))
    sys.exit(0 if not blockers else 2)


if __name__ == "__main__":
    main()
