# Mesh Baseline v1 · 优化对齐契约

> **生效口径：2026-09-02 · schema `mesh_baseline_v1.1`（校准版）**  
> 核心纪律：**禁止只说「感觉好了」；任何优化必须给 Ask + Relation 的 Δ，并明确愿意付出的交易。**  
> PR 强制流程见仓库根目录 `AI_EVAL_POLICY.md` / `CONTRIBUTING.md`。  
> **尚未正式冻结 `BASELINE_v1.json`：** 先用校准后的 `mesh_baseline.py` 跑全量，确认口径后再冻。

**先追求可重复，再追求指标完美。**  
同 corpus + 同问题集 + 同配置 → 测 → 改 → 再测 → 用 **Δ** 决定是否保留。  
环境变了 → 升小版本标签，不得与旧绝对值混比。

---

## 0. v1.1 校准（必须先修再冻）

| P0 | 规则 |
|----|------|
| Token | `total_tokens` **或** `prompt+completion`，禁止相加 |
| Ask latency | 每题 **一个** canonical e2e；阶段 latency 单独存，不算进 P50/P95 样本池 |
| Ask 通过率 | 字段名 **`ask_e2e_pass_rate`**（不是 gold Recall） |
| Rel keep / gate | **`rel_candidate_keep_rate_proxy`** / **`rel_decision_gate_accept_rate_proxy`**（不是 Recall/Precision） |
| Rel Grounding | **`n_ok / n_relations`**；`n_relations=0` → `null`（不是 1.0） |
| failure_rate | 检查/用例失败比例，不是整包 pass 的 0/1 |
| cases | Baseline JSON 必须含 Ask 逐题 + Relation candidate_ledger（可截断） |
| env | 至少：`commit` / **`git_dirty`** / `git_diff_hash` / provider / temperature / TopK / concurrency / eval hash / schema |

**HARD_BLOCKERS（与 compare 一致）：**  
`ask_grounding` · `rel_grounding` 下降 → `[BLOCKER]`；  
`rel_strong_no_evidence` · `rel_blocked_leaks` · `rel_missing_tier` 上升 → `[BLOCKER]`。

---

## 1. 为什么要有 Baseline

错误：

> 「Candidate 从 14 扩到 40，发现更多关系了。」→「好。」

正确必问：

| 必问 | 含义 |
|------|------|
| Recall ↑？ | 真漏检是否减少（有金标才算真 Recall） |
| Precision ↓多少？ | 坏卡 / 误报涨了多少 |
| Grounding 有没有下降？ | **硬底线**，见 §5 |
| 人工审核多了多少？ | blockers / 人改 / 上线耗时 |
| LLM 成本增加多少？ | token / 调用次数 |
| Latency（尤其 P95）增加多少？ | 用户尾部体验 |
| Failure / Retry Rate？ | 524、重试膨胀也是退化 |

再例：「Gate 加严，坏卡少了」→ Precision↑ Grounding↑ 之后，**Recall ↓多少？Review ↓还是 ↑？**

---

## 2. 记分板（命名含 proxy / e2e）

### 2.1 Ask

```
Ask
├ ask_e2e_pass_rate            ← 题集 pass/n；不是 gold Recall
├ ask_precision_proxy
├ ask_grounding                ← 硬底线
├ ask_p50/p95/p99_ms           ← 每题一个 canonical e2e
│   （阶段 percentiles 另存 cases.ask_latency_stages）
└ ask_token_total / Cost
```

### 2.2 Relation

```
Relation
├ rel_candidate_keep_rate_proxy   （draft/candidates；非 gold recall）
├ rel_decision_gate_accept_rate_proxy
├ rel_grounding                   ← n_ok/n_relations；硬底线
├ human_review_count
└ generation_cost_proxy
```

有金标后再加：`rel_candidate_recall_gold` / `rel_decision_precision_gold` / Ask retrieval Recall。

### 2.3 System（横向）

```
System
├ failure_rate     （checks / pytest 失败占比）
└ retry_rate       （有 n_retries/n_llm_calls 才填；否则 null）
```

### 2.4 为什么必须写 proxy / e2e

没有金标时：

```
14 candidates → 7 skip → 7 keep
```

**不能**写成 `Recall = 50%`。  
那只是系统自己的 keep 率，不是真实正确率。

| P0 字段名（代码） | 含义 | P1 升级 |
|-------------------|------|--------|
| `ask_e2e_pass_rate` | E2E case pass/n | gold retrieval recall |
| `ask_precision_proxy` | failure_layer 启发式 | gold precision |
| `rel_candidate_keep_rate_proxy` | draft / for_decision | `*_recall_gold` |
| `rel_decision_gate_accept_rate_proxy` | gate accept among keep path | `*_precision_gold` |

**禁止把「系统自己的输出分布」当成「真实正确率」写进对外结论。**

---

## 3. Baseline = 结果 + 实验条件 + 明细

`BASELINE_*.json`（schema `mesh_baseline_v1.1`）**必须**带环境冻结 + `cases`，否则 Δ 不可审计。

```json
{
  "baseline": "v1",
  "schema": "mesh_baseline_v1.1",
  "frozen_at": "…",
  "corpus": "golden",
  "ask_eval_set": "ask_eval_v1.jsonl",
  "env": {
    "commit": "abc123",
    "git_dirty": false,
    "git_diff_hash": null,
    "provider": "openai_compat",
    "llm_model": "…",
    "temperature": null,
    "embedding_model": "…",
    "prompt_hashes": {},
    "eval_dataset_hash": "…",
    "schema_version": "…",
    "topk": null,
    "rerank": null,
    "ask_concurrency": null,
    "job_concurrency": null,
    "candidate_cap": null,
    "analysis": {},
    "max_tokens": {}
  },
  "metrics": {
    "ask_e2e_pass_rate": 0.0,
    "ask_precision_proxy": 0.0,
    "ask_grounding": 0.0,
    "rel_candidate_keep_rate_proxy": 0.0,
    "rel_decision_gate_accept_rate_proxy": 0.0,
    "rel_grounding": 0.0,
    "failure_rate": 0.0,
    "retry_rate": null
  },
  "hard_blockers": {
    "higher_is_better_must_not_drop": ["ask_grounding", "rel_grounding"],
    "lower_is_better_must_not_rise": ["rel_strong_no_evidence", "rel_blocked_leaks", "rel_missing_tier"]
  },
  "cases": {
    "ask": [{ "id": "e02", "pass": true, "latency_ms": 18151, "tokens": 1234 }],
    "relation": { "candidate_ledger": [], "integrity": {} }
  }
}
```

脏工作区：`build` 默认拒绝；仅调试可 `--allow-dirty`（会写入 `git_dirty=true`）。  
可比性规则：改 model / prompt / corpus / TopK / analysis / concurrency 任一 → **新开 baseline 标签**，不得与旧 Δ 混谈「绝对进步」。

---

## 4. 现有工具 → 格子（P0）

| 格子 | P0 取数 | 备注 |
|------|---------|------|
| `ask_e2e_pass_rate` / precision_proxy / grounding | `eval/run_final_eval.py` | **不是** retrieval Recall |
| Ask latency | 每题 canonical `latency.total_ms`（或 e2e） | 阶段另存；禁止双采样 |
| Ask token | `usage.total_tokens` XOR prompt+completion | |
| keep_rate / gate_accept **proxy** | audit funnel / ledger | 禁止称 gold recall/precision |
| `rel_grounding` | integrity `n_ok/n_relations` | 0 卡 → null |
| human review | draft backlog + missing tier | Time 待埋点 |
| failure / retry | regression checks + pytest counts；Ask retries | retry 无数据 → null |

一键入口（汇总仍在补）：`deploy/run_full_regression.py`。

---

## 5. 交易规则与硬底线

### 5.1 可交易（必须写明「我接受」）

- Recall / Precision（含 proxy）  
- Latency（含 P95）  
- Token / Cost  
- Human Review（在合理范围）  
- Failure/Retry（仅小幅且有解释）  

### 5.2 原则上不可交易（release blocker）

| 硬底线 | 规则 |
|--------|------|
| **Grounding** | New &lt; Baseline → **原则上直接拒绝**（Ask 与 Relation 皆然） |
| **安全** | ⑤区/L3 泄漏、blocked 进读者、无 evidence 强关系上线 → **拒绝** |

例外只能由 owner 书面批准，并写进 PR「破例原因」。

### 5.3 标准交易声明格式

```text
本次优化：
  Ask E2E Pass Rate +8%
  Ask Precision (proxy) -2%
  Ask Grounding 0          ← 硬门槛，必须 ≥ baseline
  Ask P95 +1.4s
  Ask Token +12%
  Rel Candidate Keep Rate (proxy) +…
  Rel Gate Accept Rate (proxy) -…
  Rel Grounding 0
  Human Review -…
  Failure/Retry Rate …

我接受：
  Precision (proxy) -2%
  P95 +1.4s
  Token +12%
原因：换取 E2E Pass +8%（声明：非 gold Recall）
```

---

## 6. Latency：不要只记一个总耗时

平均 10s 可能是 P50=5s、P95=40s → 体验仍差。

### Ask（目标结构）

| 字段 | 含义 |
|------|------|
| `retrieve_ms` | 检索 |
| `source_ms` | 分析 source 步 |
| `cross_ms` | cross |
| `verify_ms` | verify |
| `generation_ms` | 成文 |
| `total_ms` | 端到端 |
| `p50` / `p95` / `p99` | 题集或滑动窗口 |

P0：每题一个 canonical `total_ms`（或 e2e），题集 **P50/P95/P99**；阶段 percentiles 单独输出。  
P1：阶段字段进 Ask eval 报告并保证覆盖率。

### Relation / Preview

至少：`cards_ms`、`draft_ms`、`decision_ms`、`writer_ms`、`total_ms`（P1 埋点；P0 用手记 total 墙钟）。

---

## 7. PR 顶部固定模板（Eval Delta）

```markdown
### Eval Delta

| Metric | Baseline | New | Δ | Tradeoff |
|--------|---------:|----:|--:|---------|
| Ask E2E Pass Rate | | | | — |
| Ask Precision (proxy) | | | | 接受 / — |
| Ask Grounding | | | | **硬门槛** |
| Ask P50 / P95 / P99 | | | | 接受 / — |
| Ask Token | | | | 接受 / — |
| Rel Candidate Keep Rate (proxy) | | | | — |
| Rel Gate Accept Rate (proxy) | | | | 接受 / — |
| Rel Grounding | | | | **硬门槛** |
| Human Review | | | | — |
| Failure Rate / Retry Rate | | | | |

**实验条件：** commit + git_dirty / corpus / model / embed / prompt hash / TopK / concurrency（与 Baseline 文件一致）  
**结论：** 接受 / 拒绝 / 仅灰度  
**交易说明：** …
```

适用范围：Chunk / FTS / Vector / Rerank / Decision / Gate / Writer / 并发 / Agent / Prompt。

---

## 8. 冻结、对比与演进

```bash
# 1) 跑回归 / Ask → report_*.json + full_regression_*.json
# 2) 校准后冻 Baseline（工作区须干净；调试才 --allow-dirty）
python deploy/mesh_baseline.py build \
  --ask-report eval/reports/report_XXXX.json \
  --regression-report eval/reports/full_regression_XXXX.json \
  [--relation-audit path/to/audit.json] \
  --out eval/reports/BASELINE_v1_<YYYYMMDD>.json

# 3) 改完再跑 → build OPTIMIZED_*.json
# 4) 自动 Δ（Grounding/safety ↓ → exit 2）
python deploy/mesh_baseline.py compare \
  eval/reports/BASELINE_v1_<YYYYMMDD>.json \
  eval/reports/OPTIMIZED_*.json \
  --md-out eval/reports/EVAL_DELTA.md
```

**可重复 > 指标完美。** 正式 `BASELINE_v1` 只在 v1.1 校准确认后冻结。

---

## 9. P0 / P1 / P2

| | 项 |
|--|-----|
| **P0** | 上表校准；`mesh_baseline_v1.1`；HARD_BLOCKERS 与 compare 一致；cases 明细 |
| **P1** | corpus/eval/git 更完整 hash；env diff 检查；阶段 latency 全覆盖；Retry 埋点 |
| **P2** | 金标 Recall/Precision；真实 cost pricing；CI 自动拦 PR（compare exit 2） |

---

## 10. 一句话

> Mesh 后续不是「继续加 AI 能力」，而是：**每增加一种能力，必须证明带来了什么、付出了什么；Grounding/安全不降，其余用标准化交易说话。**
