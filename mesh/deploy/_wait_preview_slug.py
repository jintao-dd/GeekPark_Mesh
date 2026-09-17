#!/usr/bin/env python3
from __future__ import annotations
import sys
import time
from app import job_store, preview_job

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15"
kind = sys.argv[2] if len(sys.argv) > 2 else "preview"
for i in range(180):
    st = preview_job.get_state(slug) if kind == "preview" else job_store.get(kind, slug, {})
    running = st.get("running")
    done = st.get("done")
    err = st.get("error")
    msg = st.get("message") or ""
    print(f"STATUS running={running} done={done} error={err!r} msg={msg!r}", flush=True)
    if done:
        sys.exit(0 if not err else 2)
    if err and not running:
        sys.exit(2)
    time.sleep(20)
sys.exit(1)
