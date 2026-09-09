#!/bin/bash
# Performance Baseline v1 on tmesh (healthy single-request + optional capacity)
set -euo pipefail
BASE=/opt/geekpark-tmesh
ROUNDS="${ROUNDS:-5}"
RUN_CAPACITY="${RUN_CAPACITY:-1}"
LEVELS="${LEVELS:-1,2,4,8}"

mkdir -p "$BASE/eval/reports"
docker exec geekpark-tmesh mkdir -p /srv/mesh/eval/reports

# Ensure scripts (image may not bake eval runners)
docker cp "$BASE/eval/run_agent_request_profile.py" geekpark-tmesh:/srv/mesh/eval/run_agent_request_profile.py
docker cp "$BASE/eval/run_perf_baseline_v1.py" geekpark-tmesh:/srv/mesh/eval/run_perf_baseline_v1.py

echo "==> REPRO_STATUS"
docker exec geekpark-tmesh cat /srv/mesh/data/REPRO_STATUS.json | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("REPRO_STATUS"), d.get("environment_manifest",{}).get("commit"), d.get("environment_manifest",{}).get("vector"), d.get("environment_manifest",{}).get("model"))'

echo "==> Performance Baseline v1 (rounds=$ROUNDS)"
docker exec -e MESH_AGENT_USE_LLM=1 geekpark-tmesh \
  python /srv/mesh/eval/run_perf_baseline_v1.py --reuse-env-db --rounds "$ROUNDS" \
  --out-json /srv/mesh/eval/reports/AGENT_PERF_BASELINE_V1.tmesh.json \
  --out-md /srv/mesh/eval/reports/AGENT_PERF_BASELINE_V1.tmesh.md

docker cp geekpark-tmesh:/srv/mesh/eval/reports/AGENT_PERF_BASELINE_V1.tmesh.json \
  "$BASE/eval/reports/AGENT_PERF_BASELINE_V1.tmesh.json"
docker cp geekpark-tmesh:/srv/mesh/eval/reports/AGENT_PERF_BASELINE_V1.tmesh.md \
  "$BASE/eval/reports/AGENT_PERF_BASELINE_V1.tmesh.md"

if [ "$RUN_CAPACITY" = "1" ]; then
  echo "==> Capacity C=$LEVELS"
  LEVELS="$LEVELS" TIMEOUT="${TIMEOUT:-300}" ROUNDS=1 PAUSE=5 \
    OUT_JSON="$BASE/eval/reports/AGENT_CAPACITY_PRESSURE.tmesh.sonnet_voff.json" \
    OUT_MD="$BASE/eval/reports/AGENT_CAPACITY_PRESSURE.tmesh.sonnet_voff.md" \
    bash /tmp/_run_capacity_llm.sh || bash "$BASE/deploy/_run_capacity_llm.sh" || true
fi

echo PERF_BASELINE_V1_OK
