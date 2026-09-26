# Colleague Agent v2 · Stage 2 — Colleague Core

> **日期：** 2026-09-10  
> **总览：** [`COLLEAGUE_AGENT_V2.md`](./COLLEAGUE_AGENT_V2.md)  
> **Stage 1：** ✅ 冻结（禁止再改 `colleague_controller.py` 自然语言规则 / schema）

## 职责拆分（本 Stage 的灵魂）

| 角色 | 一句话 |
|------|--------|
| **Controller** | 你现在想干什么？（已冻结） |
| **Colleague Core** | 我作为同事，该怎么理解和回应你？ |
| **Session** | 我们刚才聊到哪？（本 session） |
| **Memory（Stage 3）** | 我过去和你聊过什么？ |
| **Grounded Brain** | 公司事实到底是什么？（冻结） |

Memory **不**承担「让它看起来像人」。Stage 2 先像人；Stage 3 再了解你。

---

## Stage 2 切分

| 子阶段 | 焦点 | 验收 |
|--------|------|------|
| **2A Conversation Core** | 自然聊天 / 观点 / 情绪 / 长短 / 不机械 / 不客服腔 | 真人盲测 20～30 段：「会不会以为是普通同事在说话」 |
| **2B Self + Relationship** | 稳定身份 / 用户关系 / 偏好 / 情绪 / 自我反馈 / 纠正后状态 | 「你和傻子没区别」→「你自己感觉呢」→「我想让你成为我们同事」链 |
| **2C Opinion / Initiative** | 判断 / 下一步建议 / 适度主动 /「我觉得」 | Opinion ≠ Fact；公司事实仍走 Grounded Brain |

本文件当前执行范围：**Stage 2A**。2B/2C 未开时不堆完整 Persona Prompt。

---

## Stage 2A · 执行冻结

**禁止改：**

- `colleague_controller.py`（含自然语言规则）
- Controller schema / Hard boundary
- Retrieval / Ranking v1.4 / Claim Support v2.4c-2 / Evidence / Temporal / Published-only / Agent Contract / Gold

**升级：**

- `colleague_chat.py` — response_mode 驱动表达预算；去掉「1～3 句」+ `max_tokens=350` + `>600` 硬截断
- `colleague_core.py` — 稳定但简洁的 Colleague Core Context（**仅 session scope**）
- `session_state.py` — session 内 relationship / tone / feedback 字段
- `runtime.py` — 把 `response_mode` 传入 Conversation；clarify 也可走 Conversation LLM

**不做：** Vector Memory、跨 session 自动持久化用户信息、多层 LLM。

---

## Colleague Core Context（session）

```
self_identity / role / communication_style
relationship_context（当前仅 session）
user_tone / current_emotional_tone
current_goal / current_topic
recent_turns / user_feedback
response_mode（来自 Controller，只消费不扩 schema）
```

Self：默认知道「我是 GeekPark Mesh，内部 AI 同事」；**除非用户问，不反复自我介绍**。  
Relationship：session 内可形成 familiar / preference / feedback（如「别像机器人」→ 立刻调风格）。  
Emotion：casual / serious / frustrated / excited / neutral — 禁止强行 emoji、客服腔、过度共情、「很抱歉给您带来不便」、每轮追问还需要什么。

Opinion（2A 允许轻量）：「我觉得 / 我更倾向 / 如果是我」——须区分 FACT / OPINION / SUGGESTION；公司事实只能来自 Ask。

---

## response_mode 表达预算

| mode | 风格 | tokens 量级 |
|------|------|-------------|
| direct | 短、结论优先 | ~220 |
| conversational | 自然对话 | ~480 |
| opinion | 观点+理由 | ~650 |
| clarify | 一两句问清 | ~180 |
| explain | 按需展开 | ~700 |
| rewrite | 直接给结果 | ~800 |
| followup | 承接、不重复 | ~400 |
| abstain | 自然说明、不暴露内部 | ~220 |

---

## 性能

| 路径 | LLM |
|------|-----|
| Hard → Conversation | 0 Controller + 1 Conversation |
| Ambiguous → Conversation | ≤1 Controller + 1 Conversation |
| Enterprise | Existing Ask |
| **禁止** Colleague Core 再加一层 LLM | |

---

## 验收（2A）

不扩 Gold。三类：natural / emotional·feedback / opinion·content。

真人盲测 + tmesh。记录：

`robotic_score` · `naturalness` · `tone_alignment` · `unnecessary_explanation` · `repetition` · `response_length_fit` · `user_continuation` · `user_correction`

重点不是「答案对不对」，而是：**看起来是不是一个人在和我说话。**

质量门：Canonical / Unseen / S01–S05 / REPRO 不回退；企业事实 Evidence / Published-only 不变。

### 停止条件（2A）

- 不再普遍客服腔 / 反复解释自己  
- 回复长度自然、语气跟上下文  
- 用户能自然继续对话  

达到即停，**不要继续堆 Persona Prompt** → 再进 2B。

---

## 命令

```bash
python -m pytest tests/test_colleague_stage2a.py -q
python -m eval.run_colleague_stage2a --gate A
```

真人盲测清单：`eval/colleague_stage2a_blind.md`
