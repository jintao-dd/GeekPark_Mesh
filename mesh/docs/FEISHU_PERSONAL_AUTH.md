# 飞书个人授权（UAT）清单

> 对齐《飞书 CLI 能力介绍与最佳实践》双身份模型。  
> 更新：2026-09-11

## 一句话

- **应用身份（bot / TAT）**：公司通讯录、群成员、可见群/消息、drive 搜索、代发消息、别人 busy。  
- **个人身份（user / UAT）**：我的日历详情、以我名义建日程、个人私有文档/邮箱等「以我为视角」的数据。

授权入口：飞书里点 Agent 给出的链接 → `/auth/feishu/agent` → 回调写入 `data/feishu_uat/`。  
**开放平台需增加重定向 URL：** `{MESH_BASE_URL}/auth/feishu/agent/callback`

---

## 必须个人授权（USER / USER_PREFERRED）

| 能力 | 为什么 |
|------|--------|
| 我的日历详情 / agenda | CLI/API 个人日程视图要 UAT；bot 只能查别人 freebusy |
| 以我的名义创建/改日程 | 否则日程挂在应用/无主日历上，不符合「同事代办」 |
| 搜我的私有云文档 | `docs +search` 等为 user-only；bot 的 drive 搜索看不到个人盘 |
| 跨会话「我的」消息深度检索 | 个人可见范围 ≠ 机器人可见范围 |
| 通讯录关键词搜同事（`contact +search-user`） | CLI shortcut **仅 user**；我们用 bot 部门名册（directory）兜底 |
| 邮箱 / 个人任务 / 个人妙记视角 | 文档场景里的个人 context；开能力后同样要 UAT |

问「我的日程」且未授权 → Agent 会直接发授权链接，不假装查到了。

---

## 不需要个人授权（bot 即可）

| 能力 |
|------|
| 公司通讯录名册（部门树内姓名 + open_id，受应用通讯录权限范围） |
| 按 open_id 查人（`contact +get-user`） |
| 当前群成员列表 |
| 机器人可见群列表 / 群内消息 |
| 他人 busy / freebusy（有 open_id） |
| 应用可见云文档 drive 搜索 |
| 机器人发消息；应用身份建文档并分享给你 |

---

## 实现要点

1. `feishu_hands/identity_policy.py` — 工具/resource_type → IdentityNeed  
2. `feishu_user_auth.py` — UAT 存取 + 授权链接  
3. `tools._ensure_identity` — 缺 UAT 时返回 `user_auth_required`  
4. `feishu.calendar.propose` — 群成员 + busy 的受控多步（仍可用 bot）  
5. Hands `call_tool` 白名单 — Mesh 侧 Restrict  
