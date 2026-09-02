# AI Eval Policy · Mesh

> 研发纪律。与 `mesh/docs/MESH_BASELINE_v1.md` 配套（schema **`mesh_baseline_v1.1`**）。  
> **禁止只说「感觉好了」；必须给 Ask + Relation Δ，并写明交易。**  
> **正式 `BASELINE_v1.json` 须在口径校准后再冻。**

## 适用范围

凡改动以下任一，PR 必须带 **Eval Delta**（见 Baseline 文档模板）：

- Chunk / FTS / Vector / Rerank  
- Relation Decision / Gate / Writer / Prompt  
- Ask 检索、分析、并发、限流  
- Agent / 多步编排  
- 影响 Preview / Publish / Embedding 行为的配置  

纯文档、纯样式、与检索/生成无关的运维脚本：可豁免，但需在 PR 注明 `eval-exempt: <理由>`。

## 硬规则

1. **Grounding（Ask 与 Relation）相对 Baseline 下降 → 原则上拒绝合并。**  
2. **安全指标恶化**（`rel_strong_no_evidence` / `rel_blocked_leaks` / `rel_missing_tier` 上升）→ **release blocker**。  
3. E2E / proxy / Latency / Token / Review 可以交易，但必须在 PR 写清「我接受 … 因为 …」。  
4. 无金标前必须用正确字段名：`ask_e2e_pass_rate`、`ask_precision_proxy`、`rel_candidate_keep_rate_proxy`、`rel_decision_gate_accept_rate_proxy`；禁止写成真实 Recall/Precision。  
5. Δ 对比必须 **同 corpus + 同问题集 + 同实验条件**（含 `git_dirty`）；条件变了要升 Baseline 小版本。  
6. Latency：每题一个 canonical e2e；至少报 **P50/P95**；禁止双采样进分位数。  
7. `failure_rate` 是失败比例；`retry_rate` 无埋点时为 null，不得假装已采集。

## PR 检查清单

- [ ] 粘贴 `### Eval Delta` 表（Baseline 文档 §7）  
- [ ] 填写实验条件（或链接 Baseline / OPTIMIZED JSON；注意 `git_dirty`）  
- [ ] Grounding Δ ≥ 0（或 owner 书面破例）  
- [ ] proxy / e2e 指标名称正确  
- [ ] 写明接受的交易与原因  
- [ ] 结论：接受 / 拒绝 / 仅灰度  

## 取数与自动 Δ

```bash
python deploy/mesh_baseline.py build \
  --ask-report eval/reports/report_XXXX.json \
  --regression-report eval/reports/full_regression_XXXX.json \
  [--relation-audit path/to/audit.json] \
  --out eval/reports/BASELINE_v1.json

python deploy/mesh_baseline.py compare \
  eval/reports/BASELINE_v1.json \
  eval/reports/OPTIMIZED.json \
  --md-out eval/reports/EVAL_DELTA.md
```

脚本：`mesh/deploy/mesh_baseline.py`。Status：`[OK]` / `[WARN]` / `[BLOCKER]` / `[.]`。  
脏树默认拒绝 build；仅调试加 `--allow-dirty`。

## 取数入口（原始报告）

- Ask：`mesh/eval/run_final_eval.py`  
- 全量回归：`mesh/deploy/run_full_regression.py`  
- Relation ledger / integrity：见 `MESH_BASELINE_v1.md` §4  

## 演进

Baseline 追求 **可重复**，不追求一次指标完美。  
v1.1 校准 → 正式冻 v1 → 之后环境变更升小版本并保留旧冻结文件。
