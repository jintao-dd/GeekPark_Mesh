#!/usr/bin/env bash
# tmesh CLI Adapter smoke (R1–R3). Does not switch default backend.
set -euo pipefail
CTR="${1:-geekpark-tmesh}"

echo "==> container=$CTR"
docker exec "$CTR" sh -c 'command -v lark-cli && lark-cli --version'
docker exec "$CTR" sh -c 'echo BACKEND=$MESH_FEISHU_HANDS_BACKEND; echo CLI_APP_ID_SET=$([ -n "$LARKSUITE_CLI_APP_ID" ] && echo yes || echo no); echo BRAND=$LARKSUITE_CLI_BRAND'

# Dry-run send proves env credentials resolve as bot (no interactive login).
echo "==> dry-run im +messages-send --as bot"
docker exec "$CTR" sh -c \
  'lark-cli im +messages-send --as bot --chat-id oc_smoke_placeholder --text "cli-adapter-smoke" --dry-run --format json' \
  || echo "WARN: dry-run exited non-zero (check scopes/creds); version+env still required"

echo "CLI_ADAPTER_SMOKE_DONE"
