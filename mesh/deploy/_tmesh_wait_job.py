import sys
import time
from app import job_store

slug = "2026-8-17"
kind = sys.argv[1] if len(sys.argv) > 1 else "pipeline"
for _ in range(120):
    st = job_store.get(kind, slug, {})
    running = st.get("running")
    done = st.get("done")
    err = st.get("error")
    msg = st.get("message") or (st.get("results") or {}).get("extract") or ""
    print(f"STATUS running={running} done={done} error={err!r} msg={msg!r}", flush=True)
    if done:
        sys.exit(0)
    if err:
        sys.exit(2)
    time.sleep(20)
sys.exit(1)
