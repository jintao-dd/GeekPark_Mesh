#!/usr/bin/env python3
"""Compare key mesh files by sha256 prefix (local vs remote)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

FILES = [
    "app/relation_writer.py",
    "app/relation_decision.py",
    "app/relation_decision_audit.py",
    "app/relation_decision_consistency.py",
    "app/relation_display.py",
    "app/relation_candidates.py",
    "app/relation_gate.py",
    "app/relation_writer.py",
    "app/providers/openai_compat_provider.py",
    "app/chunk_index.py",
    "app/db.py",
    "app/embed_job.py",
    "app/job_worker.py",
    "app/main.py",
    "app/preview_job.py",
    "app/llm.py",
    "app/issue_verify.py",
    "app/attribution_verify.py",
    "app/owner_guard.py",
    "app/schema_pg.sql",
    "app/static/app.js",
    "app/static/dialog.js",
    "app/templates/admin.html",
    "deploy/_publish_readiness.py",
    "deploy/_repatch_relation_tiers.py",
    "deploy/_probe_status.py",
    "deploy/replay_relation_pipeline.py",
    "app/edm.py",
    "app/static/style.css",
    "app/templates/base.html",
    "app/templates/edm_email_inline.html",
    "app/prompts/issue_relation_decisions.md",
    "app/prompts/issue_relation_writer.md",
    "app/prompts/issue_draft.md",
    "deploy/run_full_regression.py",
]

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
for rel in FILES:
    p = root / rel
    if not p.is_file():
        print(f"MISSING  {rel}")
        continue
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    print(f"{h}  {rel}")
