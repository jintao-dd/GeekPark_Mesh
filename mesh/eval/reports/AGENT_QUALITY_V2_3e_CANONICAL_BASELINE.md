# Agent Quality v2.3e · Canonical Baseline（稳定性验证）

> **claim_support 停刀**（本轮零改动产品规则）。  
> Ranking v1.4 / Recall / Vector / Contract / Wiki / ES / Graph：**冻结或后置**。

## 1. Gold merge（已完成）

| 集 | 路径 | n | 合入 canonical |
|----|------|---|----------------|
| Answer | `eval/answer_gold_v2.jsonl` | **21** | A18–A21 |
| Evidence | `eval/evidence_gold_v2.jsonl` | **20** | E16–E20 |

扩展 accept 文件保留作历史；**主 Gold 已冻结上述对抗题**。

## 2. 合并后全量 regression（tmesh）

| Layer | 结果 | 相对冻结点 |
|-------|------|------------|
| **Answer（merged）** | **21/21 = 1.0** · fails=[] | ✅ canonical 干净 |
| **Evidence（merged）** | **20/20 = 1.0** · fails=[] | ✅ canonical 干净 |
| **Ranking v1.4** | MRR **0.831** · nDCG@10 **0.8389** · R@20 **0.9433** · **4125@5** | ✅ 与冻结一致 |
| **Recall** | R@5/10/20 = **0.826 / 0.896 / 0.943** | ✅ 与冻结 83/90/94 一致 |
| **Temporal** | **0.875**（21/24）· fail **T03/T19/T20** | ⚠️ 相对历史 24/24 有缺口（见下） |
| Attr/Relation unit | rc=1（容器无 pytest；fallback 未全绿） | ⚠️ 非 Claim 链 blocker |

JSON：`eval/reports/baselines/QUALITY_V2_3E_CANONICAL.json`

### Claim→Support 冻结判定

```
canonical Answer/Evidence hard clean ✅
worse on merged A21/E16/E18 路径 ✅
→ Claim Support v2.3e 可进入「冻结 / 回归」阶段（对已知 canonical）
```

## 3. Unseen 对抗题（诊断刀 · 未合主 Gold）

目的：区分 **claim strength 语义** vs **关键词定向匹配**。  
**本轮不改 claim_support。**

Gold：`answer_gold_unseen_v23e.jsonl`（7）+ `evidence_gold_unseen_v23e.jsonl`（8，含 E28 应回答对照）

| 集 | pass | fails |
|----|------|-------|
| Answer unseen | **0/7 = 0.0** | A22–A28 全挂 |
| Evidence unseen | **1/8 = 0.125** | E21–E27 挂；**E28 应回答 ✅** |

共性：`claim_strength=weak` · `support=supported` · 用相关端侧/面壁条目成文（**false-support**）。  
表述例：已经实现装车 / 规模化生产 / 量产车型采用 / 装车落地 / 批量交付 / 规模化量产阶段。

### 诊断结论（重要）

> v2.3e 修好的是 **已知表达族**（量产上车 / 证明…量产 / 交割…）上的 strength 门；  
> **不是**完整的「强确定性 claim」语义覆盖。  
> Unseen paraphrase 仍会把 topic overlap 升级成 `supported`。

对照：E28「锦涛做了什么」仍 `supported` 且 pass → **未见 over-abstain 误伤普通题**。

→ **不要**把 unseen 合入主 Gold，直到单独开「strength 语义扩展」刀（那是下一阶段产品决策，不是本轮）。

## 4. Temporal 0.875 说明（不在本轮修）

失败：**T03 / T19 / T20**。  
本轮容器路径可能走了成文 LLM（单题耗时长），与 Claim harness（`MESH_AGENT_USE_LLM=0`）不是同一条路径。  
**不阻塞** Claim→Support canonical 冻结；列入下一阶段「Fixed-model LLM-on baseline」一起看。

## 5. 路线状态

```
Ranking v1.4              ✅ production frozen
Claim Support v2.3e       ✅ canonical P0 frozen（已知 Gold）
Canonical Gold merge      ✅
Full regression           ✅ Answer/Evidence/Ranking/Recall
Unseen paraphrase         ⚠️ 暴露关键词边界（故意不修）
        ↓
下一阶段（未开始）：
  Fixed-model LLM-on baseline
  → Model A/B
  → Feishu / Wiki 后置
```

冻结：Retrieval · Vector · Ranking · Contract · claim_support（停刀）  
后置：ES · Wiki · Graph RAG · 模型路由

## 6. 产物

- Runner：`eval/run_quality_v2_3e_canonical.py`
- Baseline：`eval/reports/baselines/QUALITY_V2_3E_CANONICAL.json`
- Unseen：`eval/reports/UNSEEN_V23E_ACCEPTANCE.json`
- 主 Gold：`answer_gold_v2.jsonl` / `evidence_gold_v2.jsonl`
