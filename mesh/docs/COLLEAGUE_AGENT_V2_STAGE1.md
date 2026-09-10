# Colleague Agent v2 · Stage 1 — Semantic Decision Layer

> **原则：规则定义边界，模型理解语言。**

## 成功标准

> 开发人员没写过这句话，Controller 还能不能听懂。

## 路径

```
Hard boundary（仅安全且确定）
  ├─ 命中 → 0 Controller LLM
  └─ 未命中 → 1× Sonnet Controller → fixed schema
         → Conversation | Enterprise Ask | Clarify
```

Controller **只决策**，不生成最终回答。

## Hard boundary（克制）

只保留：

- permission / system / draft / capability
- meta / whoami
- 极低风险协议闭集：`谢谢` / `好的` / `收到` / `明白` / `ok`（**不含**「哈哈」）
- Session 已明确且结构完整的 follow-up（那X呢 / 还有吗 / 他后来…）

**不在 Hard：**

- 「哈哈」「今天忙死了」等口语
- 「跟谁聊过」「有哪些关系」「期次列表」
- 「X最近怎么样」

以上一律 Controller。

## 双门验收

### Gate A — CI deterministic

```bash
python -m eval.run_colleague_controller_stage1 --gate A
```

看：schema / hard-boundary / wiring / fallback / mock LLM  
指标：`controller_decision_accuracy` / `mode_accuracy` / `needs_grounding_accuracy` / `response_mode_accuracy` + runtime 行为。

### Gate B — tmesh semantic（真实 Sonnet）

```bash
# 镜像需含 eval runner；或在宿主机：
python -m eval.run_colleague_controller_stage1 --gate B
# 或 deploy/_tmesh_controller_gate_b.sh（docker cp eval 后 exec）
```

验证：未见自然语言 + 上下文多轮，Controller 真理解。

## 通过条件（同时）

| 层 | 条件 |
|----|------|
| Controller | decision / mode / grounding 准确 |
| Runtime | retrieval_when_unneeded↓、missed_grounding 不升 |
| 性能 | 明确路径 0 Controller LLM；ambiguous ≤1 |
| 质量 | Canonical / Unseen / S01–S05 / REPRO 不回退 |
| 泛化 | Gate B 未见表达仍能判对 |

通过后 **停止打 Stage 1**，进入 Stage 2 · Persona / Conversation。
