#!/bin/bash
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python -c "
from app import job_store
import json
for kind in ('pipeline', 'preview'):
    st = job_store.get(kind, '2026-8-17', {})
    print(kind, json.dumps({k: st.get(k) for k in ('running','done','error','phase','message','cur','total')}, ensure_ascii=False))
"
