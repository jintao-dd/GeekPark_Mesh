#!/usr/bin/env bash
# Sync relation pipeline to prod containers (no preview/pipeline).
set -euo pipefail
BASE="${MESH_DEPLOY_REMOTE_BASE:-/opt/geekpark-mesh}"
APP_FILES=(
  relation_candidates.py
  relation_decision.py
  relation_decision_audit.py
  relation_decision_consistency.py
  relation_display.py
  relation_writer.py
  relation_verify.py
  llm.py
  edm.py
  preview_job.py
  owner_guard.py
)
PROMPT_FILES=(
  issue_relation_decisions.md
  issue_relation_writer.md
  issue_relation_narratives.md
)

for f in "${APP_FILES[@]}"; do
  test -f "$BASE/app/$f" || { echo "missing $BASE/app/$f"; exit 1; }
done
for f in "${PROMPT_FILES[@]}"; do
  test -f "$BASE/app/prompts/$f" || { echo "missing $BASE/app/prompts/$f"; exit 1; }
done
test -f "$BASE/app/providers/openai_compat_provider.py"

for c in geekpark-mesh geekpark-mesh-worker; do
  echo "==> $c"
  for f in "${APP_FILES[@]}"; do
    docker cp "$BASE/app/$f" "$c:/srv/mesh/app/$f"
  done
  for f in "${PROMPT_FILES[@]}"; do
    docker cp "$BASE/app/prompts/$f" "$c:/srv/mesh/app/prompts/$f"
  done
  docker cp "$BASE/app/providers/openai_compat_provider.py" \
    "$c:/srv/mesh/app/providers/openai_compat_provider.py"
done

docker restart geekpark-mesh geekpark-mesh-worker
sleep 12

docker exec -w /srv/mesh geekpark-mesh env PYTHONPATH=/srv/mesh python - <<'PY'
from app.relation_candidates import build_relation_candidates
from app.relation_decision import apply_evidence_gate, build_relations_two_phase
from app.relation_decision_consistency import normalize_relation_label
from app import llm
assert hasattr(llm, "build_relation_decisions_with_coverage")
import inspect
src = inspect.getsource(apply_evidence_gate)
assert "gate_warning" in src
assert "_skip(GATE_CODE_DECISION_INCONSISTENT" not in src
text = open("/srv/mesh/app/relation_candidates.py", encoding="utf-8").read()
assert "_build_routing_candidates" in text
print("import_ok", normalize_relation_label("同一条赛道，各自在做"))
print("gate_warn_only_ok")
print("routing_ok")
PY

docker exec geekpark-mesh-worker ls \
  /srv/mesh/app/relation_decision.py \
  /srv/mesh/app/relation_writer.py \
  /srv/mesh/app/prompts/issue_relation_decisions.md

echo "SYNC_PROD_RELATION_OK"
