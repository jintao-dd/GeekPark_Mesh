#!/bin/bash
# redact secrets — only print booleans/lengths
python3 - <<'PY'
import os, re
from pathlib import Path
envp = Path('/opt/geekpark-mesh/.env')
vals = {}
if envp.exists():
    for line in envp.read_text(errors='replace').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1)
        vals[k.strip()]=v.strip().strip('"').strip("'")
# also from running container
import subprocess
out=subprocess.check_output(['docker','exec','geekpark-mesh','env'], text=True, errors='replace')
cenv={}
for line in out.splitlines():
    if '=' in line:
        k,v=line.split('=',1); cenv[k]=v
secret=(cenv.get('MESH_SECRET') or vals.get('MESH_SECRET') or '')
print('MESH_SECRET_set', bool(secret))
print('MESH_SECRET_is_default', secret in ('', 'change-me-please'))
print('MESH_SECRET_len', len(secret))
print('MESH_ADMIN_PASSWORD_set', bool(cenv.get('MESH_ADMIN_PASSWORD') or vals.get('MESH_ADMIN_PASSWORD')))
print('MESH_OWNER_PASSWORD_set', bool(cenv.get('MESH_OWNER_PASSWORD') or vals.get('MESH_OWNER_PASSWORD')))
print('FEISHU_enabled', bool(cenv.get('FEISHU_APP_ID') and cenv.get('FEISHU_APP_SECRET')))
print('SMTP_set', bool(cenv.get('SMTP_HOST') and cenv.get('SMTP_USER')))
PY
