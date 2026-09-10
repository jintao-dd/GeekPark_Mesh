# Colleague Agent v2 · Stage 1 — Semantic Decision Layer

> **状态：** ✅ 收口（Gate A + Gate B 通过）。**停止继续打 Stage 1。**  
> **总览心智模型：** 见 [`COLLEAGUE_AGENT_V2.md`](./COLLEAGUE_AGENT_V2.md)（Controller 不是 UX 中心）。

## 成功标准（已验证）

> 即使用户用了一句开发人员没有提前想到的话，Controller 仍能正确理解他在做什么，并选择自然的处理路径。

## 架构

```
message + SessionContext
        │
        ▼
 hard boundary? ──yes──► 0 Controller LLM → schema
        │ no
        ▼
 1× Controller LLM (Sonnet / task=controller)
        │
        ▼
 fixed schema → runtime
        ├─ Conversation → colleague_chat
        ├─ Enterprise   → Existing Ask
        └─ Clarify      → 问清楚
```

Controller **只决策**：不回答、不 Retrieval、不造公司事实、不把上轮答案当 Truth。

## Schema（形状冻结）

```json
{
  "mode": "conversation|enterprise|followup|clarify|meta|system",
  "intent": "...",
  "entities": [],
  "topic": "",
  "needs_grounding": true,
  "needs_clarification": false,
  "response_mode": "conversational|direct|clarify|opinion|rewrite|explain|abstain|followup",
  "context_refs": [],
  "confidence": "high|medium|low"
}
```

`needs_grounding=true` → Existing Ask；`false` → Conversation LLM。

## Hard boundary（极少）

- permission / system / draft / capability
- meta / whoami
- 极小协议闭集：谢谢 / 好的 / 收到 / 明白 / ok（**不含「哈哈」**）
- Session 足够时的结构 follow-up（那 X 呢 / 他后来 / 还有吗）

**禁止**继续加 soft phrase regex。「哈哈」、闲聊、企业问句自然语言 → Controller。

## 性能

| 路径 | Controller LLM |
|------|----------------|
| hard | 0 |
| semantic / ambiguous | ≤1 |
| 禁止多层 LLM pipeline | |

## Gate（双门）

| Gate | 作用 |
|------|------|
| **A** CI + mock | schema / hard / wiring / fallback；决策准确率 + runtime 行为 |
| **B** tmesh + 真 Sonnet | 未见表达 / 自然语言 / 上下文语义 |

指标同时看：

- Controller：`controller_decision_accuracy` / `mode_accuracy` / `needs_grounding_accuracy` / `response_mode_accuracy`
- Runtime：`wrong_route` / `retrieval_when_unneeded` / `missed_grounding`

```bash
python -m eval.run_colleague_controller_stage1 --gate A
# Gate B：mesh/deploy/_tmesh_controller_gate_b.sh
```

## 成文限制（已移交 Stage 2A）

Conversation 旧护栏（1～3 句 / 350 tokens / 600 字截断）已在 Stage 2A 拆除，改为 response_mode 预算。见 `COLLEAGUE_AGENT_V2_STAGE2.md`。

## 冻结

Retrieval / Ranking v1.4 / Claim v2.4c-2 / Ontology / Gold / Multi-Agent / ReAct / 长期 Memory / Wiki·ES·Graph  
**以及：** 不再扩 Controller 规则 / schema / intent。

镜像：`geekpark-mesh:2026-09-10-1ffe3abc0d3a`
