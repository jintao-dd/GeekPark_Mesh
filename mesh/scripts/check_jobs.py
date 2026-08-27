#!/usr/bin/env python3
"""Check in-memory pipeline/preview jobs (run inside container)."""
from app import pipeline, preview_job, db

con = db.connect()
issues = con.execute(
    "SELECT slug, period_label, status FROM issues ORDER BY date_end DESC LIMIT 8"
).fetchall()
con.close()

print("=== issues ===")
for r in issues:
    print(dict(r))

print("=== pipeline JOBS ===")
if not pipeline.JOBS:
    print("(empty)")
else:
    for slug, st in pipeline.JOBS.items():
        print(slug, {k: st.get(k) for k in ("running", "done", "error", "cur", "token")})

print("=== preview JOBS ===")
if not preview_job.JOBS:
    print("(empty)")
else:
    for slug, st in preview_job.JOBS.items():
        print(
            slug,
            {k: st.get(k) for k in ("running", "done", "error", "phase", "message", "cur", "total")},
        )

running = []
for slug, st in pipeline.JOBS.items():
    if st.get("running"):
        running.append(("pipeline", slug, st.get("message") or st.get("error") or f"step {st.get('cur')}"))
for slug, st in preview_job.JOBS.items():
    if st.get("running"):
        running.append(("preview", slug, st.get("message") or st.get("phase") or st.get("error")))

print("=== RUNNING ===")
if running:
    for kind, slug, msg in running:
        print(f"{kind} · {slug} · {msg}")
else:
    print("none")
