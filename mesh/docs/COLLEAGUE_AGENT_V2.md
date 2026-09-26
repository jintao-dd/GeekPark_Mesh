# Colleague Agent v2 · 总览（重新梳理）

> **状态：** v2 组件化路径已由 **v3 Wave 1** 旁路。现行主文档：[`COLLEAGUE_AGENT_V3.md`](./COLLEAGUE_AGENT_V3.md)。  
> **日期：** 2026-09-10  
> **一句话（历史）：** Controller 只决定「这句话怎么处理」——该中轴已废弃。

---

## 0. 心智模型（先读这段）

| 层 | 职责 | 是不是 UX 中心 |
|----|------|----------------|
| **Hard boundary** | 安全且确定的事：0 Controller LLM | 否 |
| **Controller** | 这句话 → Conversation / Enterprise Ask / Clarify | **否**（只决策，不回答） |
| **Conversation LLM** | 闲聊、观点、润色、情绪、像同事 | **是**（飞书里用户直接感到） |
| **Existing Ask** | 公司事实：Published + Evidence | **是**（事实答对） |
| **Grounded Brain** | Retrieval / Ranking / Claim / Ontology / Gold | 冻结，禁止为本层解冻 |

**原则**

1. **规则定义边界，模型理解语言** — Hard boundary 极克制；自然语言交给 Controller。
2. **Controller 不生成最终回答** — 只出固定 schema；禁止再堆规则、再堆 intent、再堆 schema 字段。
3. **体验中心在下游** — 「像不像同事」看 Conversation；「事实对不对」看 Ask。Stage 1 验证的是「没写过的话 Controller 还能听懂」；Stage 2 才真正做 Persona。

```
用户消息
   │
   ▼
Hard boundary ──命中──► 0 Controller LLM → schema → Runtime
   │未命中
   ▼
1× Controller（Sonnet，task=controller）
   │ 只决策，不成文
   ▼
fixed schema
   ├─ Conversation  → Conversation LLM（成文）
   ├─ Enterprise    → Existing Ask（成文）
   └─ Clarify       → 问清楚（成文）
```

性能门：明确路径 **0** Controller LLM；模糊路径 **≤1**。禁止多层 LLM pipeline / ReAct / Multi-Agent。

---

## 1. 两条产品轨（不要混）

| Track | 内容 | 状态 |
|-------|------|------|
| **A · Colleague** | 飞书同事体验：听懂 → 路由 → 自然成文 | Stage 1 ✅ → Stage 2 |
| **B · Weekly Report** | 周报生产 / Preview / Owner | 独立，不跟 Colleague 抢刀 |

质量轨（Ranking / Claim / Gold）**冻结**，不为 Agent 体验解冻。

---

## 2. Stage 地图

| Stage | 主题 | 要验证的一句话 | 状态 |
|-------|------|----------------|------|
| **1** | Semantic Decision | 开发没写过这句话，Controller 还能不能听懂 | ✅ 收口；Controller **冻结** |
| **2A** | Conversation Core | 听懂之后，说得像不像普通同事 | ← **现在**；见 `COLLEAGUE_AGENT_V2_STAGE2.md` |
| **2B** | Self + Relationship | 纠正链 / 偏好 / 自我反馈 | 未开 |
| **2C** | Opinion / Initiative | 观点与适度主动；Opinion ≠ Fact | 未开 |
| **3** | Memory | 记住你们过去说过什么（不负责「像人」） | 未开 |
| **4** | Collaboration | 主动帮你做事 | 未开 |
| **5** | Enterprise Memory | 企业记忆 | 未开 |
| **6** | Production Runtime | 生产化 | 未开 |

**Stage 1 已停。** 禁止再改 `colleague_controller.py` 自然语言规则 / 扩 schema。体验问题进 Stage 2A Conversation。

---

## 3. Hard boundary（极克制）

只做「安全且确定」：

- permission / system / draft / capability
- meta / whoami（已明确）
- **极小协议闭集**：谢谢 / 好的 / 收到 / 明白 / ok（**不含「哈哈」**）
- Session 已明确且结构完整的 follow-up（那 X 呢 / 还有吗 / 他后来…）

其余一律进 Controller（含「哈哈」、闲聊、观点、润色、「跟谁聊过」「X 最近怎么样」等自然语言）。

禁止：再往 Hard 里加几十种口语 regex。

---

## 4. Controller schema（冻结形状）

Controller **只决策**：

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

- `needs_grounding=true` → Existing Ask  
- `needs_grounding=false` → Conversation LLM  
- **不** Retrieval、**不**造公司事实、**不**把上轮答案当 Truth、**不**对用户成文  

**冻结：** 不为单 case 加字段；不为 Persona 扩 Controller。

---

## 5. 成文与字数（Stage 2A）

体验卡在下游 Conversation，不在 Controller。

### Conversation（`colleague_chat.py` + `colleague_core.py`）

| 项 | 现状 |
|----|------|
| 表达预算 | **response_mode 驱动**（direct / conversational / opinion / clarify / explain / rewrite / followup / abstain） |
| 旧护栏 | ❌ 已移除「通常 1～3 句」+ 固定 `max_tokens=350` + `>600` 硬截断 |
| soft cap | 仅防失控（conversational ~1400 字量级），正常对话不应触达 |
| Colleague Core | session 内 identity / relationship / tone / feedback（**非**长期 Memory） |
| 模型 task | `answer` |

### Enterprise Ask

| 项 | 默认 | Env |
|----|------|-----|
| 成文 max_tokens | 700 | `MESH_ANSWER_MAX_TOKENS` |
| 证据正文裁剪 | 280 字/条 | `MESH_ANSWER_BODY_CHARS` |

### Controller

`max_tokens=400`，`json_mode=True` — 只出 schema；**冻结，不成文。**

---

## 6. Gate（Stage 1 双门，已定）

| Gate | 环境 | 证明什么 |
|------|------|----------|
| **A** | CI + mock LLM | schema / fallback / wiring / hard-boundary；可复现 |
| **B** | tmesh + 真 Sonnet | 未见表达 / 自然语言 / 上下文；模型真听懂 |

同时看：

- **Controller 决策**：`controller_decision_accuracy` / `mode_accuracy` / `needs_grounding_accuracy` / `response_mode_accuracy`
- **Runtime 行为**：`wrong_route` / `retrieval_when_unneeded` / `missed_grounding`

```bash
# Gate A（本地 / CI）
python -m eval.run_colleague_controller_stage1 --gate A

# Gate B（tmesh）
# 见 mesh/deploy/_tmesh_controller_gate_b.sh
```

---

## 7. 关键代码

| 文件 | 角色 |
|------|------|
| `app/agent/colleague_controller.py` | Hard + Controller 决策 |
| `app/agent/runtime.py` / `intent.py` | 编排进 Runtime |
| `app/agent/colleague_chat.py` | Conversation 成文（**UX 中心之一**） |
| `app/agent/conversation.py` | Hard follow-up / meta 辅助；**不是**自然语言分类器 |
| `app/agent/session_state.py` | SessionContext |
| `app/agent/feishu_reply.py` | 飞书 UX 包装 |
| `app/llm.py` | `task=controller` / `answer` 模型路由 |

文档：

- 本页：总览与心智模型  
- `COLLEAGUE_AGENT_V2_STAGE1.md`：Stage 1 细节与冻结项  
- `COLLEAGUE_AGENT_V1.md`：v1 规则路由历史（已被 Stage 1 语义层取代决策路径）

---

## 8. 明确不做

- 继续往 Controller 堆 soft regex / intent / schema  
- Controller 生成最终用户回答  
- ReAct / Planner / Multi-Agent / Graph / ES / Wiki / 长期 Vector Memory / Dynamic Router  
- 解冻 Ranking / Claim / Gold 为「像同事」打补丁  
- Stage 1 已通过后继续打 Stage 1

---

## 9. 下一刀

**Stage 2A · Conversation Core**（进行中）→ 见 `COLLEAGUE_AGENT_V2_STAGE2.md`。

| 角色 | 职责 |
|------|------|
| Controller | 你现在想干什么？（冻结） |
| Colleague Core | 作为同事该怎么回应 |
| Session | 刚才聊到哪 |
| Memory（Stage 3） | 过去聊过什么 |
| Grounded Brain | 公司事实 |

2A 停条件达到后再进 2B Self/Relationship → 2C Opinion/Initiative → Stage 3 Memory。
