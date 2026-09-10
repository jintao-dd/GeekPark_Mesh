#!/usr/bin/env bash
# Gate B on tmesh: copy eval harness into container, run live Controller LLM
set -euo pipefail
CTR="${1:-geekpark-tmesh}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker exec "$CTR" mkdir -p /srv/mesh/eval
docker cp "$ROOT/eval/run_colleague_controller_stage1.py" "$CTR:/srv/mesh/eval/run_colleague_controller_stage1.py"
docker cp "$ROOT/eval/colleague_controller_stage1.jsonl" "$CTR:/srv/mesh/eval/colleague_controller_stage1.jsonl"
docker exec -e PYTHONPATH=/srv/mesh "$CTR" python -m eval.run_colleague_controller_stage1 --gate B
docker cp "$CTR:/srv/mesh/eval/reports/COLLEAGUE_CONTROLLER_STAGE1_GATE_B.json" "$ROOT/eval/reports/COLLEAGUE_CONTROLLER_STAGE1_GATE_B.json" 2>/dev/null || true
docker cp "$CTR:/srv/mesh/eval/reports/COLLEAGUE_CONTROLLER_STAGE1_GATE_B.md" "$ROOT/eval/reports/COLLEAGUE_CONTROLLER_STAGE1_GATE_B.md" 2>/dev/null || true
echo "GATE_B_DONE"
