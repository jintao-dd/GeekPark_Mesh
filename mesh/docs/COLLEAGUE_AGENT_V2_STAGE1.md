# Colleague Agent v2 · Stage 1 — Semantic Decision Layer

> **原则：规则定义边界，模型理解语言。**  
> 不是：规则定义语言，模型负责执行。

## 成功标准

不是「枚举了多少说法」，而是：

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
```

Controller **只决策**：不回答、不 Retrieval、不造公司事实、不把上轮答案当 Truth。

## Schema

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
- 协议级短确认（谢谢/哈哈/好的…）
- Session 足够时的结构 follow-up（那X呢 / 他后来 / 还有吗）
- 明确企业问法（跟谁聊过 / 有哪些关系 / 期次列表）
- 「X最近怎么样」结构歧义 → clarify

**禁止**继续加 soft phrase regex（忙死了 / 靠谱吗 / 我想看看…那边）。

## 性能

| 路径 | Controller LLM |
|------|----------------|
| hard | 0 |
| semantic / ambiguous | ≤1 |
| 禁止多层 LLM pipeline | |

## Gate

```bash
python -m eval.run_colleague_controller_stage1
```

看：wrong_route / retrieval_when_unneeded / missed_grounding / clarification / response_mode / context_error / hard 路径不得打 Controller LLM。

场景含：自然语言、未见表达、≥5 组上下文多轮（mock LLM 保证 CI 可复现）。

## 冻结

Retrieval / Ranking v1.4 / Claim v2.4c-2 / Ontology / Gold / Multi-Agent / ReAct / 长期 Memory / Wiki·ES·Graph
