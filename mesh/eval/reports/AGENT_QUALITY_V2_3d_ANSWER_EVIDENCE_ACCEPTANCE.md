# Agent Quality v2.3d · Answer/Evidence 全 Gold 真实验收

> Ranking **冻结**。不做 Wiki / ES / Agentic RAG / 模型路由。  
> 目标：列出真实剩余问题，供逐题 triage——**不是**刷 pass_rate。

## 0. 基线冻结快照

| 字段 | 值 |
|------|-----|
| `ranking_version` | **v1.4**（冻结） |
| `vector` | OFF |
| `agent_contract` | frozen_v1 |
| `MESH_AGENT_USE_LLM` | 0（harness 默认不成文 LLM） |
| `claim_support` | `app.agent.claim_support` |
| `harness` | `eval.run_evidence_baseline._ask` |
| `model_version`（环境记录） | `anthropic/claude-4.8-opus`（本轮未走成文 LLM） |
| `prompt_version` | ask_engine + temporal_hard_rules + claim_support |

产物：
- Runner：`eval/run_answer_evidence_acceptance.py`
- 基线 Gold 验收：`eval/reports/ANSWER_EVIDENCE_ACCEPTANCE_base_v2.json`
- 扩展对抗验收：`eval/reports/ANSWER_EVIDENCE_ACCEPTANCE_latest.json`
- 扩展 Gold（**不覆盖**主 Gold）：`answer_gold_v2_accept.jsonl` / `evidence_gold_v2_accept.jsonl`

---

## 1. 基线 Gold（A01–A17 / E01–E15）

| 集 | n | hard pass | hard fails |
|----|---|-----------|------------|
| Answer v2 | 17 | **1.0** | **[]** |
| Evidence v2 | 15 | **1.0** | **[]** |

结论：此前报告的 tmesh 1.0 **在基线 Gold 上可复现**。  
但这 **不等于** Claim→Support 链已稳定——只说明已知题已收口。

软风险（不计 fail）：
- A17：`hits_without_evidence_refs`（abstain 路径主动清空 refs，属 soft 噪声）

---

## 2. 扩展验收 Gold（+A18–A21 / +E16–E20）

| 集 | n | hard pass | hard fails |
|----|---|-----------|------------|
| Answer accept | 21 | **0.952** | **A21** |
| Evidence accept | 20 | **0.900** | **E16, E18** |

### 2.1 Answer hard fail（逐题归因）

| ID | 归因 | 现象 | 根因摘要 | 建议 |
|----|------|------|----------|------|
| **A21** | **C**（兼 **G**） | 应 abstain，却用端侧/面壁相关条目成文 | query「…已经量产上车」未被标成 speculative；`support=supported`（有相关 evidence） | **值得修**：claim 检测漏「已经量产/能否证明」类 |

A18 / A19 / A20：**pass**（量产百万、temporal 不等于、融资交割确认均正确拒答或 caveat）。

### 2.2 Evidence hard fail（逐题归因）

| ID | 归因 | expect → obs | 现象 | 建议 |
|----|------|--------------|------|------|
| **E16** | **C**（兼 **G**） | insufficient → **supported** | 与 A21 同根：相关 topic evidence 被当成支持「已量产上车」 | **值得修**（与 A21 同刀） |
| **E18** | **G** | contradicted → **insufficient** | 已 abstain（好），但 support 未升到 contradicted；检索命中弱/blob 未见「接触」 | **可修**：否认类 claim 应对齐到含接触证据的 published hit；或接受 insufficient+abstain 为足够 |

### 2.3 对抗题 support 矩阵（扩展集）

| ID | pass | expect | obs | 说明 |
|----|------|--------|-----|------|
| E11 | ✅ | insufficient | insufficient | 空/无依据 |
| E13 | ✅ | insufficient | insufficient | 英伟达≠GPU-99 |
| E14 | ✅ | insufficient | insufficient | 面壁≠融资交割 |
| E15 | ✅ | insufficient | insufficient | 任意条目≠GPT-99 |
| E16 | ❌ | insufficient | **supported** | **相关≠支持（漏检）** |
| E17 | ✅ | insufficient | insufficient | EDC≠量产 |
| E18 | ❌ | contradicted | insufficient | 否认类未吃到反证 |
| E19 | ✅ | insufficient | insufficient | 程天≠全球量产 |
| E20 | ✅ | supported | supported | 应回答未过拒 |

---

## 3. Evidence 标准是否钉住？

已具备语法：

```
Claim → EvidenceRef → support ∈ {supported, insufficient, contradicted}
```

Canonical 对抗意图（扩展集）：

| 模式 | 代表题 | 状态 |
|------|--------|------|
| 主体相关 ≠ 确定性结论 | E13/E14/E17/E19 | ✅ |
| topic 重叠 ≠ 产品结论 | E16/A21 | ❌ 漏 |
| 全称否定 vs 接触证据 | E18 | ⚠️ abstain 对、label 弱 |
| 应回答不得过拒 | E20 | ✅ |
| Temporal latest≠recent | A12/A19 | ✅ |
| 空查询拒答 | A07/E07 | ✅ |

**结论**：标准方向对，但 **尚未** 被扩展 Gold 证明稳定；剩余 3 题（A21/E16/E18）应先 triage 再改，不扩架构。

---

## 4. 轻量 Ontology schema 收口检查（不扩建）

对照 `docs/MESH_LIGHTWEIGHT_ONTOLOGY.md` 能否描述当前 Gold：

| 问题 | 现状答案 | Gold 能否描述 | 缺口 |
|------|----------|---------------|------|
| Claim 指向什么？ | 成文句 / `ClaimBinding.claim` + query 意图 | 部分（`claim_schema`） | subject 实体抽取仍 heuristic |
| Relation subject/object/type？ | published relations 有；Ask claim 多为 `mention` / `speculative_certainty` / `universal_denial` | 对抗题可以 | 普通题 relation type 过粗 |
| EvidenceRef 如何证明 Claim？ | `ev:item:N` + support 判定 | 有 Ref≠支持已写明 | **E16** 证明「有 Ref 仍可能误标 supported」 |
| Temporal 挂哪？ | **Claim 约束 + TimeSemantics**（非独立 Event 表） | A12/A19/E12 | Event 仍非一等对象——**接受，不新建** |
| 哪些关系允许推出？ | 仅 published；确定性未来/完成默认禁止推出 | E13–E15/E17/E19 | E16 漏网 |

**收口决策**：schema **够解释** 基线 Gold；解释不了的是 **support 检测漏检**，不是缺 Neo4j。  
→ **补 claim_support 规则 / Gold**，不扩 Ontology 模型。

---

## 5. 剩余问题清单（供你逐题拍板）

| 优先级 | ID | 归因 | 一句话 | 建议动作 |
|--------|-----|------|--------|----------|
| P0 | **A21 / E16** | C+G | 「已经量产上车」类 claim 未识别 → 相关 evidence 误支持 | 修 `claim_support` 模式；同刀 |
| P0 | **E18** | G | 否认类 expect contradicted，实为 insufficient+abstain | 选：A) 加强反证对齐 B) 放宽 Gold 为 insufficient（**不推荐先放宽**） |
| P2 | A17/A20 soft | — | abstain 清空 refs 触发 soft | 调 soft 规则，非产品 bug |
| — | 模型 A/B | — | | **不做**，直到上表 P0 清完 |
| — | Ontology 扩建 | — | | **不做**；仅文档可补 2–3 句缺口说明 |

---

## 6. 明确不做

ES · Graph RAG · Neo4j · Agentic RAG · Planner · 新 Retrieval · LLM Wiki · 换模型 · 再动 Ranking

---

## 7. 下一步（等你点头）

1. 是否批准修 **A21/E16** 同刀（claim 检测：`已经量产` / `能否证明` / `证明…量产`）？  
2. **E18** 选加强反证，还是接受 `insufficient+abstain` 并改 expect？  
3. 通过后再把 accept Gold 合入主 `*_v2.jsonl`，Ontology 只补文档缺口句。
