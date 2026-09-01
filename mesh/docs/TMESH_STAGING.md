# tmesh 测试环境

与生产 **mesh.geekpark.ai** 完全隔离的 staging，用于 eval、KG 抽检 publish、Verify 回归。

| 项 | 生产 mesh | 测试 tmesh |
|----|-----------|------------|
| 目录 | `/opt/geekpark-mesh` | `/opt/geekpark-tmesh` |
| 域名 | mesh.geekpark.ai | **tmesh.geekpark.net** |
| 端口 | 8090 | **8091** |
| PG 库 | mesh | **tmesh**（独立 volume） |
| regen/publish 抽检 | **禁止** | 允许（`MESH_ALLOW_PROD_PUBLISH=1`） |

## 首次部署（服务器）

```bash
cd /opt/geekpark-mesh   # 或上传 deploy/setup_tmesh.sh
bash deploy/setup_tmesh.sh
# 可选：从生产克隆 PG 数据（不影响生产）
bash deploy/setup_tmesh.sh --clone-prod-db
```

## DNS

添加 A 记录：`tmesh.geekpark.net` → 服务器公网 IP（与 mesh 相同）。

HTTPS：已为 `tmesh.geekpark.net` 配置 ZeroSSL 证书（acme.sh），HTTP 自动跳转 HTTPS。`.env` 中 `MESH_BASE_URL=https://tmesh.geekpark.net`。

## 常用命令

```bash
cd /opt/geekpark-tmesh
docker compose -f docker-compose.staging.yml ps
docker compose -f docker-compose.staging.yml logs -f mesh
docker exec geekpark-tmesh python eval/run_final_eval.py --corpus prod
docker exec geekpark-tmesh python deploy/prod_kg_spotcheck.py --slug 2026-8-17 --audit-only
```

## 更新代码

从本机同步 app 到 tmesh（不碰生产）：

```bash
rsync -av mesh/app/ root@SERVER:/opt/geekpark-tmesh/app/
ssh root@SERVER 'cd /opt/geekpark-tmesh && docker compose -f docker-compose.staging.yml restart mesh mesh-worker'
```

## 原则

- **生产 mesh**：只读 eval，禁止脚本 publish（见 `PROD_DATA_POLICY.md`）
- **测试 tmesh**：可 regen merge、owner 流程演练、全链路 eval
