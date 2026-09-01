#!/usr/bin/env bash
# 在生产容器内运行最终验收（PG 全库）。
# 用法（服务器）：
#   cd /opt/geekpark-mesh && bash deploy/run_final_acceptance_prod.sh
set -euo pipefail
cd "$(dirname "$0")/.."
CONTAINER="${MESH_CONTAINER:-geekpark-mesh}"

echo "==> 同步 eval 脚本到容器"
docker cp eval "${CONTAINER}:/srv/mesh/eval"
docker cp deploy/run_final_acceptance_prod.sh "${CONTAINER}:/srv/mesh/deploy/" 2>/dev/null || true

echo "==> 生产 PG 统计"
docker exec "$CONTAINER" python -c "
from app import db
from eval.eval_lib import db_stats
c=db.connect()
print(db_stats(c))
"

echo "==> 24 题检索 + E2E + follow-up + SSE"
docker exec "$CONTAINER" python eval/run_final_eval.py --corpus prod --e2e --followup --sse

echo "==> 报告路径（容器内）"
docker exec "$CONTAINER" ls -la /srv/mesh/eval/reports/ | tail -5

echo "DONE: 请 docker cp 报告到宿主机或查看 eval/reports/FINAL_ACCEPTANCE_REPORT.md"
