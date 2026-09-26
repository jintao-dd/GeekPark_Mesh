# GeekPark Mesh · Colleague Agent v1

> **历史壳层。** 当前总览与心智模型见 [`COLLEAGUE_AGENT_V2.md`](./COLLEAGUE_AGENT_V2.md)；决策路径已由 Stage 1 Semantic Controller 取代。  
> 产品层：Conversation Intelligence Layer  
> Grounded Brain（Retrieval / Ranking v1.4 / Claim Support v2.4c-2 / Evidence / Temporal / Published-only / Ask Contract / Ontology / Gold）**冻结不动**。

## 目标

把「检索型 Agent」升级成内部员工愿意长期用的 **AI 同事**：懂人话、记得当前话题、知道何时查公司事实、何时直接聊天、何时澄清；观点可自由表达，公司事实必须 Published + Evidence。

## 架构

```
Feishu → Identity / Permission / Session
       → Conversation Intelligence（规则路由，非 Dynamic Router）
       → route
```

| Route | 行为 | LLM 次数 |
|-------|------|----------|
| `general_conversation` | 闲聊 / 观点 / 润色 → `colleague_chat`（Answer LLM） | 1 |
| `meta` | 你是谁 / 能干什么 → Conversation LLM（whoami 身份仍确定性） | ≤1 |
| `ask` / `relations` / `list` | existing Mesh Ask | existing Ask |
| `followup` | Session 消解指代 → rewrite → Ask（重新 Retrieval + Evidence） | existing Ask |
| `clarify` / ambiguous | 自然语言澄清，不盲目 Retrieval | 0 |
| `refuse` / system | 能力边界 / ACL / 草稿拒绝 | 0 |

**禁止**：Router→Memory→Retrieval→Evidence→Answer 多层 LLM；ReAct / Planner / Multi-Agent / Graph / ES / Wiki / 长期 Vector Memory / Dynamic Router。

## SessionContext

字段：`active_entities` / `active_team` / `active_issue` / `active_topic` / `active_period` / `last_*` / `unresolved_references` / `conversation_mode` / `recent_turns` / `topic_stack`

规则：

1. Memory 只理解上下文与指代，**不是**企业事实来源  
2. Follow-up 必须重新 Ask  
3. 企业 → 闲聊时 `push_topic`；「对了，高德呢」可 `restore_topic` + rewrite  

## 关键代码

- `app/agent/session_state.py` — SessionContext  
- `app/agent/conversation.py` — 规则路由  
- `app/agent/colleague_chat.py` — 同事人格 + 1× Answer LLM  
- `app/agent/runtime.py` — 编排  
- `app/agent/feishu_reply.py` — Failure / Answer UX（不暴露 claim_strength 等内部字段）  

## 场景测

`tests/test_colleague_agent_v1_scenarios.py`（不扩 Gold）

## 验收（真人 Canary）

见 `AGENT_CANARY_2.md`：关注 task_resolved / natural_followup / repair_success / repeat_question / teach_the_bot / correction / abandoned / wrong_route / latency。
