# RRF 发布记录 · 向量全开 Ranking 校准

- 日期：2026-09-22
- 环境：tmesh（`geekpark-tmesh`）
- 镜像：`geekpark-mesh:2026-09-22-9fdccae3c66a`
- digest：`sha256:4b5765996999165dfc11a88aca66b608c6c988e763a038048f496bdd76035a4e`
- commit：`9fdccae`（feat(retrieval): RRF fusion + disable ranking_quality + coverage fallback）
- 配置 B（生产默认）：`MESH_FUSION=rrf` · `MESH_RANKING_QUALITY=0` · LIKE 兜底词长加权覆盖率 · 向量可开

## 生产配置（B）

| 项 | 值 |
|---|---|
| 融合 | RRF（`MESH_FUSION=rrf`，legacy 可 A/B） |
| ranking_quality 四层补丁 | **关闭**（默认 `MESH_RANKING_QUALITY=0`） |
| LIKE 兜底 | `_fallback_score`：查询词**词长加权覆盖率**（非平坦 -1.2） |
| 通道权重 | `item_entity_facts=0.5`，其余 1.0 |
| 向量 | tmesh 已开；prod 按发布开关 |

## 关向量词法拆分（已完成，归因用）

| 臂 | MRR | nDCG@10 | P@5 | R@5 |
|---|---|---|---|---|
| A legacy + quality ON | 0.6333 | 0.5701 | 0.3378 | 0.5256 |
| B RRF + quality OFF | 0.5944 | 0.5597 | 0.3378 | 0.5256 |
| C RRF + quality ON | 0.6167 | 0.5652 | 0.3378 | 0.5256 |

- A→B：MRR **-0.0389**（整包）
- B→C：MRR **+0.0223**（补丁边际）
- A→C：MRR **-0.0166**（RRF 固有代价）
- 结论：关补丁与 RRF 各贡献一部分 MRR 损失。

## 向量全开校准（本记录主体）

Gold：`ranking_gold_v1.jsonl` · n=30 · `MESH_RECALL_USE_EMBED=1`

| 臂 | MRR | nDCG@10 | P@5 | R@5 | R@10 | R@20 |
|---|---|---|---|---|---|---|
| A legacy + quality ON | 0.6167 | 0.5640 | 0.2333 | 0.5256 | 0.5667 | 0.5917 |
| **B RRF + quality OFF** | **0.5944** | **0.5479** | **0.2333** | **0.5256** | 0.5517 | 0.5917 |
| C RRF + quality ON | 0.5944 | 0.5462 | 0.2267 | 0.5189 | 0.5750 | 0.5750 |

### Tradeoff（A→B，向量全开）

| 指标 | Δ |
|---|---|
| MRR | **-0.0223**（轻于关向量口径 -0.0389） |
| nDCG@10 | -0.0161 |
| P@5 | **0**（持平） |
| R@5 | **0**（持平） |
| R@10 | -0.0150 |
| R@20 | 0 |

### B→C（向量全开下重开补丁）

补丁 **几乎无效**（MRR +0），且略伤 P@5/R@5 → **继续关补丁正确**。

### 逐题（向量全开）

| 题 | A | B | C | 说明 |
|---|---|---|---|---|
| R11 | 1.00 | 0.50 | 0.50 | RRF 固有代价，补丁救不回 |
| R16 | 0.50 | 0.33 | 1.00 | 补丁可回血，但整包 C 仍不优于 B |
| R19 | 1.00 | 1.00 | 0.33 | 开补丁反而掉点 |

召回侧（此前同镜像、向量开）：chunk R@10 **0.15 → 0.31**，Top10 含向量题数 **16/30 → 29/30**。
用小幅 item 级 MRR 换向量进池，是本次发布的核心交易。

## Gate

```json
{
  "p5_ok": true,
  "r5_ok": true,
  "mrr_delta": -0.0223,
  "ndcg_delta": -0.0161,
  "mrr_not_worse_than_lexical": true,
  "pass": true
}
```

**GATE PASS** → 允许上生产（B 配置）。

## 上线后观察窗（24h）

| 项 | 约定 |
|---|---|
| 窗口 | 发布后 **24 小时** |
| 预期 | 离线向量全开 A→B MRR 跌幅约 **-0.022** |
| 告警线 | 若复跑/抽样 ranking MRR 跌幅 **显著超出 -0.022**（建议阈值：**≤ -0.05** 相对发布前基线），或 P@5/R@5 出现可复现下滑 |
| 动作 | **回滚镜像**到发布前 tag；`MESH_FUSION=legacy` 可作热开关应急（不改镜像） |
| 复跑 | 见下方脚本；prod 只读库 `--reuse-env-db` |

热开关（应急，无需重建镜像）：

```bash
# 回退融合到旧路径
MESH_FUSION=legacy
# 可选：临时重开旧补丁（不推荐与 RRF 同开）
MESH_RANKING_QUALITY=1
```

正式回滚：compose 切回上一 `geekpark-mesh:<prev-tag>` 并 recreate。

## 可复跑脚本（已入库，勿再写一次性 shell）

```bash
# 容器内 / 本机（需可达 PG + embed）
cd /srv/mesh   # 或 mesh/
PYTHONPATH=. MESH_RECALL_USE_EMBED=1 MESH_EMBED_ENABLED=1 MESH_VECTOR_ENABLED=1 \
  python eval/run_ranking_calib_vec.py --reuse-env-db

# 只对比已有 latest
python eval/run_ranking_calib_vec.py --reuse-env-db --compare-only
```

依赖同目录：`run_ranking_baseline.py` · `quality_metrics.py` · `ranking_gold_v1.jsonl`。

## 归档文件

| 文件 | 说明 |
|---|---|
| `CALIB_VEC_GATE.json` | Gate 结论 |
| `RANKING_calib_vec_{A,B,C}_latest.json` | 三臂完整报告 |
| `../../run_ranking_calib_vec.py` | 可复跑校准入口 |

## 生产发布（已执行）

| 项 | 值 |
|---|---|
| 时间 | 2026-09-22 |
| commit | `8da5456` |
| image | `geekpark-mesh:2026-09-22-8da5456e10d6` |
| digest | `sha256:82fa39d6e4a81b11740fc4569f36bb29c7edc56382053b57f75809733901c039` |
| REPRO | PASS |
| 向量 | ON（`MESH_EMBED/VECTOR=1`） |
| 冒烟 | `home=307`（非 404） |
| 入口 | https://mesh.geekpark.ai |
| 观察窗 | 发布起 **24h**；MRR 跌幅告警线相对基线 **≤ -0.05** → 回滚 |

回滚镜像 tag（本发布前）：以服务器上上一成功 baseline 为准；应急热开关 `MESH_FUSION=legacy`。

## 结论

1. 生产走 **B**（RRF + 关补丁 + 覆盖率兜底）。
2. 向量全开 Gate **PASS**；MRR 代价可接受，且轻于关向量口径。
3. **已上 prod**；开 **24h** 观察；超阈值回滚镜像或 `MESH_FUSION=legacy`。
4. 校准脚本已入库，后续复现勿再手写临时脚本。
