#!/usr/bin/env bash
# Pull one issue from prod into tmesh. Usage:
#   bash deploy/pull_prod_issue_to_tmesh.sh 2026-8-17 [--preview]
set -euo pipefail
SLUG="${1:-2026-8-17}"
DO_PREVIEW=0
[[ "${2:-}" == "--preview" ]] && DO_PREVIEW=1
TMP="/tmp/prod_issue_${SLUG}.json"
RESTORE="/tmp/_restore_issue_tmesh.py"

echo "==> dump prod slug=$SLUG"
docker exec -w /srv/mesh geekpark-mesh env PYTHONPATH=/srv/mesh \
  python deploy/_dump_issue_json.py "$SLUG" "$TMP"

docker cp "geekpark-mesh:$TMP" "$TMP"
docker cp "$TMP" "geekpark-tmesh:$TMP"

echo "==> restore into tmesh"
docker cp /opt/geekpark-tmesh/deploy/_restore_issue_from_json.py \
  geekpark-tmesh:/srv/mesh/deploy/_restore_issue_from_json.py
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh \
  python deploy/_restore_issue_from_json.py "$TMP"

if [[ "$DO_PREVIEW" == "1" ]]; then
  echo "==> start tmesh preview"
  docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh \
    python deploy/_tmesh_start_preview_slug.py "$SLUG"
fi
echo "PULL_PROD_TO_TMESH_OK"
