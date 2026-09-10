# Colleague · 数据隔离与短期工作记忆

> 非长期向量记忆。调度读这些槽决定「这轮做什么」。

## 数据隔离（硬门）

| 桶 | source_tier | 用途 |
|----|-------------|------|
| published | `published` | 已上线周报事实 |
| feishu_live | `feishu_live` | Hands 读/写飞书 |
| speak_draft | `model` | 闲聊/成文草稿，不进事实池 |

禁止跨桶拼成一条「事实」；合成提示必须按 tier 分口吻。

## 短期工作记忆（会话级 TTL）

| 字段 | 含义 |
|------|------|
| `recent_turns` | 近轮对话 |
| `pending_write` | 待确认写操作 |
| `active_goal` | 未完成用户目标 `{summary, family, tool, utterance}` |
| `last_block` | 上一刀失败 `{code, tool, message}` |

`family`: `feishu_write` | `ask_published` | `ask_feishu` | `speak`

## 调度纪律

1. 有 `pending_write` → 确认/取消优先于闲聊  
2. 有 `active_goal.family=feishu_write` 且用户在催 → 回到 prepare/confirm，禁止虚晃 speak  
3. `speak` 不得声称已调用 Hands / 已创建成功  
4. 工具失败 → 记 `last_block`（`scope_denied` / `hands_off` / `empty` / `feishu_api`）并固定收口  

## 云文档默认权限

创建 docx/sheet 后：`link_share_entity=tenant_readable`（公司内获链可读）。
