#!/usr/bin/env bash
set -euo pipefail
SRC="/opt/geekpark-mesh/app/qa_structured.py"
cp "$SRC" /opt/geekpark-tmesh/app/qa_structured.py
for c in geekpark-mesh geekpark-mesh-worker geekpark-tmesh geekpark-tmesh-worker; do
  docker cp "$SRC" "$c:/srv/mesh/app/qa_structured.py"
done
docker restart geekpark-mesh geekpark-mesh-worker geekpark-tmesh geekpark-tmesh-worker
echo "QA_STRUCTURED_FIX_OK"
