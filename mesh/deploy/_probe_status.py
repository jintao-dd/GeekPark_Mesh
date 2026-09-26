#!/usr/bin/env python3
import json
import os
import time
import requests
from app import db, job_store

slug = "2026-8-17"
st = job_store.get("preview", slug, {})
print("preview_job", json.dumps({k: st.get(k) for k in ("running", "done", "error", "phase", "message")}, ensure_ascii=False))

con = db.connect()
row = con.execute(
    "SELECT running, done, error, updated_at FROM mesh_jobs WHERE job_kind='preview' AND job_key=?",
    (slug,),
).fetchone()
print("mesh_jobs", dict(row) if row else None)
con.close()

base = os.environ.get("MESH_LLM_BASE_URL", "").rstrip("/")
key = os.environ.get("MESH_LLM_API_KEY", "")
model = os.environ.get("MESH_LLM_MODEL", "")
if base and key and model:
    t0 = time.time()
    try:
        r = requests.post(
            base + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "ok"}]},
            timeout=120,
        )
        print("llm_probe", {"status": r.status_code, "latency_s": round(time.time() - t0, 1), "body_head": r.text[:120]})
    except Exception as e:
        print("llm_probe_error", str(e))
