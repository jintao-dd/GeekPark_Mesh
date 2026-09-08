# Agent Architecture v1

> 阶段切换：2026-09-08  
> **前提**：Mesh v1 候选 Go（`MESH_V1_AGENT_GO_NOGO.md`）。周报生产链与 Agent **分轨**——不再把 Agent 需求混回 Pipeline / Preview / Publish。  
> **禁止**：先做 ReAct / LLM Planner / 自由工具环。  
> **允许**：在 Published 边界之上，先定 Organization → Identity → Permission → Context。

---

## 0. 阶段声明


| 轨道                        | 状态        | 做什么                               |
| ------------------------- | --------- | --------------------------------- |
| Mesh v1（周报）               | **收口维护**  | 修阻塞 bug / 已知限制 backlog；不加新 AI 能力  |
| **Agent Architecture v1** | **当前主阶段** | 组织、身份、权限、上下文、Tool Contract、飞书 MVP |



| 层级             | 状态                                    |
| -------------- | ------------------------------------- |
| ① Organization | ✅ **冻结**（含飞书通讯录外部组织事实源）               |
| ② Identity     | ✅ **冻结**（通讯录 + Mesh 叠加；§4）              |
| ③ Permission   | ✅ **冻结**（§5；Tool ACL ⊥ Visibility ⊥ Query Scope） |
| ④ Context      | ✅ **冻结**（§6：IssueRef 生命周期）              |
| ⑤ Entity/Evidence/Relation | ✅ **冻结**（§7）                   |
| ⑥ Tool Contract | ✅ **冻结**（§8；4 Tool）                 |
| ⑦ Agent         | ✅ **架构冻结**；🔨 **实现/契约测试中**（先 Harness，后飞书） |
| ⑧ 飞书 MVP      | ⛔ 等 ⑦ 测试报告；只接线不扩大脑              |


Mesh 提供：**Published 事实底座 + Ask/Relation 工具能力 + canonical_team 业务语义**。  
Agent 提供：**解析「谁在问」之后，再调这些工具**。

---



## 1. Agent 必须先能回答的 7 个问题


| #   | 问题         | 归属层               | v1 答案来源（原则）                                                  |
| --- | ---------- | ----------------- | ------------------------------------------------------------ |
| 1   | 我们是谁？      | Organization      | 租户 `geekpark`；组织树来自**飞书通讯录**，非 Mesh 手维                       |
| 2   | 我是谁？       | Identity          | 飞书 `open_id` → 通讯录用户 + Mesh `users` 绑定                       |
| 3   | 我属于哪个团队？   | Identity          | 飞书 Department → **映射** → `canonical_team`；再与 `users.team` 和解 |
| 4   | 我能看什么？     | Permission        | Data Visibility（资格）∩ Published-only；队名聚焦属 Query Scope |
| 5   | 当前是哪一期？    | Context           | `IssueRef`：explicit → pinned → latest_published           |
| 6   | 哪些数据是正式事实？ | Context + Mesh 边界 | **仅** Published；Draft / Sources 原文默认不可见                      |
| 7   | 哪些能力可以调用？  | Tool Contract     | Permission 白名单 Tool；无 Publish / 无读原文                         |




### 1.1 门禁规则


| 状态                                        | 允许            | 禁止                              |
| ----------------------------------------- | ------------- | ------------------------------- |
| Identity / Permission / Context **未解析完整** | 不需身份权限的固定系统问答 | 调用**受限 Tool**                   |
| 解析 **完整**                                 | 白名单受限 Tool    | 仍禁止绕过 Published / Publish / 读原文 |


---



## 2. 分层路线（顺序固定）

```
① Organization     ✅ 冻结（§3）
    ↓
② Identity         ✅ 冻结（§4）
    ↓
③ Permission       ✅ 冻结（§5）
    ↓
④ Context          ✅ 冻结（§6）
    ↓
⑤ Entity/Evidence/Relation  ✅ 冻结（§7）
    ↓
⑥ Tool Contract         ✅ 冻结（§8）
    ↓
⑦ Agent                 ✅ 冻结（§9）
    ↓
⑧ 飞书 MVP
```

---



## 3. ① Organization v1 — ✅ 冻结

> **冻结结论不变**：Organization ≠ Permission；`canonical_team` 仍是业务事实标准；placeholder/external 硬规则保留。  
> **本修订（允许写入冻结稿）**：组织人树**不**由 Mesh 手维；飞书通讯录为外部组织事实源。



### 3.0 外部组织事实源（冻结修正）

**飞书组织架构作为外部组织事实源；Mesh 不复制维护原始组织架构，仅维护「飞书 Department → Mesh canonical_team」的映射与业务语义。**

```
              飞书通讯录（Contact V3）
           Department / User API
                      │
                      ↓
               Organization Sync
           （只读缓存 / 按需拉取，非手维主档）
                      │
         ┌────────────┴────────────┐
         │   GeekPark Agent Org      │
         │                           │
         │  Department 引用（飞书 ID） │
         │  Dept → Mesh Team 映射     │  ← Mesh 唯一要维护的组织配置
         │  Person 视图（运行时投影） │
         └────────────┬────────────┘
                      ↓
                 Mesh Context
```


| 我们 **不**做                     | 我们 **做**                                            |
| ----------------------------- | --------------------------------------------------- |
| 手维整套「公司 → 部门 → 人员」主档          | 调用飞书通讯录 API 读部门/用户                                  |
| 另造一套 Agent Team / Person 库当权威 | 维护 **Department → canonical_team** 映射表              |
| `飞书部门名 == Mesh team` 裸相等      | `canonical_team` / `owner_team` 业务语义（含 placeholder） |


**为何不能裸相等：** 飞书部门与 Mesh 业务团队不必 1:1。例如「内容中心·数据聚合」是 Mesh **placeholder 通道**，不是正式业务 Team；周报 `owner_team` 有独立语义。

**现实限制：** API「能」获取 ≠ 应用「已」获权。通讯录接口受**应用通讯录授权**与**字段权限**约束；根部门 / 全员架构尤其需要相应权限。Identity / Sync 必须处理：权限不足、字段缺失、部分部门不可见 → 降级到 Mesh `users.team` 或 `bound_team_missing`，**禁止**臆造部门。

参考能力（实现时对接，不在本文展开 SDK）：

- 部门列表 / 子部门：`GET /open-apis/contact/v3/departments`、`.../departments/{id}/children`  
- 部门详情：名称、父部门、负责人、成员数等  
- 用户：组织相关字段（需对应字段权限）



### 3.1 设计原则

1. **组织树：飞书为真源；业务队名：Mesh** `canonical_team` **为真源。**
2. **Mesh 只维护映射 + 业务 kind（business / placeholder / external），不维护飞书部门树副本为权威。**
3. **Organization ≠ Permission。**
4. **ChatBinding 不改写 Person** — 群默认视角 ≠ 成员组织归属。
5. **IssuePeriod 属 Context** — 非组织结构。



### 3.2 对象模型

```
Organization (tenant: geekpark)
  │
  ├─ FeishuDepartment[]     # 来自通讯录；Mesh 不手维
  │     └─ mapping → canonical_team?   # 可空=未映射
  │
  ├─ Team[]                 # Mesh 业务团队词表（ingest.TEAMS + kind）
  │
  └─ Person                 # 运行时视图：Feishu User ⊕ Mesh users（非第二套人事库）

IssuePeriod / ChatBinding   # 同前；属 Context / 会话
```



#### Team（Mesh 业务语义 — 保留）


| 字段               | 说明                                                   |
| ---------------- | ---------------------------------------------------- |
| `canonical_name` | `ingest.TEAMS` + `normalize_team` / `canonical_team` |
| `kind`           | `business` | `placeholder` | `external`              |


**business：** 编辑部、商业化团队、硅谷 BD 团队、Global Partnership 团队、英文站、品牌创意团队、社群、投资团队、音频播客团队、视频号团队、CEO / 总裁办。

#### 硬规则：placeholder / external（不变）


| kind          | 示例           | **不得**成为                                   | **可以**              |
| ------------- | ------------ | ------------------------------------------ | ------------------- |
| `placeholder` | 内容中心·数据聚合、其他 | `Person.primary_team`；默认 Team 视角；内部 ACL 主体 | 材料/拆段标签             |
| `external`    | 外部媒体         | 同上                                         | Published 中的外部实体/来源 |


映射表**禁止**把飞书部门映射到 placeholder/external 作为 Person.primary_team；若误配，Identity 视为无效。

#### Department → Mesh Team 映射（Mesh 唯一组织配置）


| 字段                     | 说明                                    |
| ---------------------- | ------------------------------------- |
| `feishu_department_id` | 飞书部门 ID                               |
| `canonical_team`       | 映射到的 Mesh business team；可空表示「不同步为业务队」 |
| `note`                 | 人工备注（如「飞书名≠Mesh 名」）                   |


同步策略 v1：按需或定时只读拉取部门列表以**辅助配置映射 UI**；权威仍是飞书 + 映射表，不是 Mesh 里再存一棵可编辑组织树。

#### Person（运行时投影）


| 字段                        | 来源优先级（原则）                                  |
| ------------------------- | ------------------------------------------ |
| `feishu_open_id`          | 入站 / 通讯录                                   |
| `person_id` / `mesh_role` | Mesh `users`（**角色以 Mesh 为准**）              |
| `display_name`            | Mesh `users.display` 优先，否则通讯录姓名            |
| `feishu_department_ids`   | 通讯录用户字段（可能因权限缺失为空）                         |
| `mapped_teams`            | 各部门经映射得到的 business `canonical_team` 列表（去重） |
| `mesh_users_team`         | `canonical(users.team)`，若有效 business       |
| `primary_team`            | **和解结果**，见 §4.4；**不是**独立存储真源               |


Duty / IssuePeriod / ChatBinding：语义同冻结稿；Duty 不授数据权；ChatBinding 不改 Person。

### 3.3 明确不做

- 在 Mesh 手维完整公司→部门→人员主档  
- 飞书部门名字符串等于 Mesh team  
- 无通讯录权限时伪造组织树  
- 用映射把人挂到 placeholder/external  
- 往 Organization 继续加 HR / 汇报线产品

---



## 4. ② Identity v1 — 飞书消息如何变成 Person

> 目标：入站消息 → **IdentityResult** → Permission。  
> 本层不做 ACL、不调检索。



### 4.1 主链路（修订）

```
飞书消息
   ↓
open_id
   ↓
① Mesh 绑定：users.feishu_open_id → users.id / role / team
   ↓
② 飞书通讯录（若已授权）：User → department_ids
   ↓
③ Department → canonical_team 映射 → mapped_teams[]
   ↓
④ 和解 primary_team（§4.4）
   ↓
⑤ 叠加 mesh_role = users.role
   ↓
IdentityResult → Permission
```

端到端语义：

```
Feishu Identity (open_id + Contact)
        ↓
Person 视图
        ↓
Mesh Team (canonical，经映射/和解)
        ↓
Mesh Role (users.role)
        ↓
Permission
```

Web：session → `users`；无 open_id 时**跳过**通讯录部门，仅用 `users.team` 投影。

### 4.2 IdentityResult


| 字段                    | 说明                                                               |
| --------------------- | ---------------------------------------------------------------- |
| `status`              | §4.3                                                             |
| `person`              | 运行时 Person 视图                                                    |
| `mesh_user_id`        | `users.id`                                                       |
| `feishu_open_id`      | 入站，始终保留                                                          |
| `mesh_role`           | **仅来自** `users.role`（通讯录不授 Mesh 操作角色）                            |
| `primary_team`        | 和解后的单一 business team；无效则 null                                    |
| `mapped_teams`        | 通讯录映射得到的候选                                                       |
| `mesh_users_team`     | `users.team` 投影（若有效）                                             |
| `team_source`         | `feishu_map` | `mesh_users` | `both_agree` | `conflict` | `none` |
| `contact_sync`        | `ok` | `skipped_no_scope` | `error` | `web_no_feishu`            |
| `bind_state`          | `linked` | `unlinked` | `ambiguous` | `open_id_mismatch`         |
| `channel` / `chat_id` | 群 `chat_id` **不**写入 primary_team                                 |
| `display_hint`        | 展示用，不升权                                                          |




### 4.3 status 枚举


| status                | 含义                                                                  |
| --------------------- | ------------------------------------------------------------------- |
| `bound`               | Mesh 已绑定；`primary_team` 有效 business                                 |
| `bound_team_missing`  | 已绑定；映射与 `users.team` 皆无有效 business team                             |
| `bound_team_conflict` | 已绑定；`mapped_teams` 与 `mesh_users_team` 均为有效但**不一致**（或映射出多个互斥队且无法独选） |
| `unlinked`            | open_id 无 Mesh 用户                                                   |
| `ambiguous`           | 多行 users / 无法唯一                                                     |
| `open_id_mismatch`    | 声明 user 与 open_id 不一致                                               |
| `anonymous_web`       | Web 未登录                                                             |


`bound` / 冲突态是否开受限 Tool → **Permission** 定（Identity 只标 `team_source`）。

### 4.4 primary_team 和解（规范性）

```
resolve_primary_team(mapped_teams, mesh_users_team):
  M = business teams from mapping (filter placeholder/external)
  U = mesh_users_team if valid business else null

  if contact_sync != ok:
      # 无通讯录权限/字段：只信任 Mesh users.team
      return (U, team_source=mesh_users|none)

  if len(M) == 1 and (U is null or U == M[0]):
      return (M[0], feishu_map or both_agree)
  if len(M) == 0 and U:
      return (U, mesh_users)
  if len(M) == 1 and U and U != M[0]:
      return (null, conflict)  # status=bound_team_conflict；两值都放入 Result 供管理员看
  if len(M) > 1:
      if U in M:
          return (U, both_agree)   # users.team 在映射集合内 → 用它消歧
      return (null, conflict)
  return (null, none)
```

**要点：**

- **角色**：永远 `users.role`，通讯录不覆盖。  
- **队名业务标准**：永远落到 `canonical_team`，不是飞书部门展示名。  
- **users.team**：保留为 Mesh 侧覆盖 / 通讯录不可用时的回落 / 多部门消歧锚点——**不是**与飞书并行的第二套组织主档。  
- 禁止 ChatBinding、禁止 LLM 猜队。



### 4.5 解析算法（修订）

```
resolve_identity(inbound):
  oid = inbound.open_id

  if channel == web and session_user:
      row = session users row
      team, src = resolve_primary_team([], canonical(row.team))
      return IdentityResult(... contact_sync=web_no_feishu, team_source=src, ...)

  rows = users WHERE feishu_open_id = oid
  if len==0 → unlinked
  if len>1 → ambiguous
  row = rows[0]
  if claimed_user_id mismatch → open_id_mismatch

  mapped, contact_sync = [] , skipped_no_scope
  if contact_permission_granted:
      deps = feishu_contact.get_user_departments(oid)  # may fail
      mapped = map_departments_to_canonical(deps)

  team, src = resolve_primary_team(mapped, canonical(row.team))
  if src == conflict → status=bound_team_conflict
  elif not team → status=bound_team_missing
  else → status=bound

  return IdentityResult(mesh_role=row.role, primary_team=team, ...)
```

Bot 入站默认 **不** `upsert` 新 users（避免幽灵账号）；开通走 OAuth / 管理员。

### 4.6 麻烦场景（更新）


| 场景                      | 判定                                                                                |
| ----------------------- | --------------------------------------------------------------------------------- |
| 未绑定 Mesh                | `unlinked`                                                                        |
| open_id 与声称用户不一致        | `open_id_mismatch`                                                                |
| 通讯录无权限 / 无部门字段          | `contact_sync=skipped_no_scope`；仅 `users.team`；仍可能 `bound` 或 `bound_team_missing` |
| 映射与 `users.team` 冲突     | `bound_team_conflict`                                                             |
| 多部门映射出多个 business team  | 用 `users.team` 消歧；否则 conflict                                                     |
| 群绑定 A、个人 primary_team B | Identity 输出 B；A → Context.`chat_team_view`                                        |
| 一人多账号                   | `ambiguous`                                                                       |
| 映射到 placeholder         | 忽略该映射；不进 primary_team                                                             |




### 4.7 与 Mesh 代码对齐


| 已有                                         | Identity v1                 |
| ------------------------------------------ | --------------------------- |
| `users` + `feishu_open_id`                 | Mesh 绑定与 **role** 真源        |
| `canonical_team` / `TEAMS`                 | 映射目标与 kind                  |
| **新建** `feishu_department_team_map`（或等价配置） | Department → canonical_team |
| 飞书 Contact V3 客户端                          | 只读；受应用权限约束                  |
| `feishu_chat_bindings`                     | 仅 Context，不进 primary_team   |
| `upsert_feishu_user`                       | OAuth/后台用；Bot 消息默认不建号       |




### 4.8 明确不做

- 手维组织树当权威  
- 昵称 / LLM 匹配身份  
- 通讯录自动改写 `users.role`  
- 无权限时假装已同步全员架构



### 4.9 验收标准

- [ ] 有映射 + 单部门 → `primary_team` 来自 feishu_map  
- [ ] 无通讯录权限 + 有 `users.team` → mesh_users 回落  
- [ ] 映射与 users.team 冲突 → `bound_team_conflict`  
- [ ] placeholder 映射不生效  
- [ ] ChatBinding 不影响 primary_team  
- [ ] unlinked / ambiguous / mismatch 稳定  

---



## 5. ③ Permission v1 — ✅ 冻结

> **冻结。** 除非 ④/⑤/⑥ 发现硬矛盾，否则不往 ①②③ 加字段/规则。  
> 输入：`IdentityResult` + channel/chat + `TeamScopeRequest?`  
> 输出：`PermissionDecision`（本层不实现、不调检索）

### 5.0 三栏独立 + 最高边界

```
IdentityResult
        ↓
PermissionDecision
  ├─ Tool ACL              # 有没有资格调用这个 Tool
  ├─ Data Visibility       # 有没有资格访问这类 Published 数据
  └─ Query Scope           # 本次默认看哪一队（聚焦，不是「只能看」）
                ↓
         Published-only    # 最高数据边界（恒成立）
```

| 栏 | 回答 | 不是 |
|----|------|------|
| Tool ACL | 能否调该 Tool | 数据范围 |
| Data Visibility | 能否访问「全公司/某类」Published | 本轮聚焦点 |
| Query Scope | 本轮聚焦哪一队 | 「只能看该队」硬 ACL（除非 Visibility 另行收紧） |

**v1 Visibility：** `bound` → 允许全公司 Published。队名/「我们」只改 Query Scope。  
**explicit ≠ 授权：** `TeamScopeRequest` → Visibility 校验后再写 Query Scope。

### 5.1 PermissionDecision

| 字段 | 说明 |
|------|------|
| `agent_access` | 是否允许进入 Agent 通道（原 allow_agent_chat） |
| `tool_acl` | 允许的 tool_id |
| `data_visibility` | fact_surface=published；published_corps；forbid draft/raw/unpublished |
| `query_scope` | mode + team_focus |
| `deny_reason` | 收紧原因 |
| `published_only` | 恒 true |

### 5.2 Tool ACL（摘要）

| tool_id | 要「我的团队」语义？ |
|---------|---------------------|
| `system.help` | 否 |
| `ask.published` | 否 |
| `ask.published.my_team` | **是** |
| `ask.relations_summary` / `context.list_issues` | 否 |
| console / publish / raw.read | **永不进 Agent** |

### 5.3–5.6 失败态（摘要）

| Identity | Permission |
|----------|------------|
| `bound` | 开 ask.*；Query Scope 正常 |
| `bound_team_missing` | **上下文缺失≠没数据权**；可 unfocused published；禁 my_team |
| `bound_team_conflict` | **默认 deny** 数据 Tool；禁猜队 |
| unlinked / ambiguous / mismatch / anonymous | 仅系统/引导 |

Tool 执行序：① ACL → ② Visibility（含 TeamScopeRequest）→ ③ Query Scope → ④ Published-only。

---

## 6. ④ Context v1 — ✅ 冻结

> **冻结。** IssueRef 生命周期（explicit → pinned → latest_published → none）、pin/lock、切期、`issue_epoch` 失效、scope_key 隔离已定。除非 ⑤/⑥ 硬冲突，不再改 ①～④。  
> Context **不重判**权限；组装流水线与字段见既定条文（组装 → IssueRef → pin/lock → 切期 → 失效矩阵 → trace）。

**冻结要点（备忘）：**

- `locked=true` 仅 explicit /「锁定这期」；latest **不**自动 lock  
- 切期须显式且唯一 published；slug 变 → `issue_epoch++` → 旧 hits/analysis 失效  
- 换 team focus **不**丢 issue pin  
- 「看哪一期」≠「默认落到最近一期」

---

## 7. ⑤ Entity / Evidence / Relation v1 — ✅ 冻结

> **冻结（含 ClaimBinding 边界钉死）。** 不建图谱；只定 EntityRef / EvidenceRef / RelationRef / ClaimBinding。  
> 除非 ⑥/⑦ 硬冲突，不再改 ①～⑤。

### 7.0 四对象（备忘）

| 对象 | 职责 |
|------|------|
| EntityRef | 人/公司/团队是谁（Published 投影） |
| EvidenceRef | 依据句柄（Published；禁 sources 原文） |
| RelationRef | 已发布关系卡 + evidence[] |
| ClaimBinding | 答案句与 Evidence 的**支持关系记录** |

### 7.1 ClaimBinding 边界（冻结修正）

ClaimBinding **不是**新的事实判定器，**不**引入 truth score / confidence 模型。

```
Claim
  ↓
EvidenceRef[]
  ↓
既有 Verification / Claim Check / citation 口径
  ↓
ClaimBinding.status = grounded | weak | unsupported
```

建议形态：

```json
{
  "claim": "...",
  "evidence_refs": ["ev_123", "ev_456"],
  "status": "grounded",
  "reason": "..."
}
```

| status | 含义 |
|--------|------|
| grounded | 有明确 Published Evidence 支持 |
| weak | 仅弱支持，不足以强述 |
| unsupported | 无可接受证据 → **不得**当作用户可见事实 |

ClaimBinding 只表达「这句话挂了哪些证据、按既有规则属于哪档支持」；**真伪裁决仍归现有 verify / Claim Check / citation，不在 ⑤ 另起炉灶。**

---

## 8. ⑥ Tool Contract v1 — ✅ 冻结

> **冻结。** 第一批仅 4 个只读 Tool：`system.help` / `context.list_issues` / `ask.published` / `ask.relations_summary`。  
> **不再扩 Tool 数量**，直到契约按下列验收通过。禁止 draft/raw/publish、禁止绕过契约查库。完整 I/O 见既定条文。

### 8.0 冻结验收标准（必须全部成立）

流水线：`Identity → ACL → Scope/IssueRef → Tool Execute → Published-only → Evidence/Result`

| # | 验收项 |
|---|--------|
| 1 | 未绑定用户不能访问数据 Tool（仅 `system.help` 类） |
| 2 | `bound_team_conflict` 默认不能访问数据 Tool |
| 3 | Tool 明确拒绝 draft / raw / unpublished |
| 4 | IssueRef 不允许绕过 Context 指定非法期次（含 draft） |
| 5 | team scope 不得绕过 Permission（须 TeamScopeRequest / Visibility） |
| 6 | 成功结果可带 Evidence / Result Ref（及 ask.* 的 ClaimBinding 字段） |
| 7 | Agent 不能通过 Tool 直接查库（仅注册 Tool 入口） |

---

## 9. ⑦ Agent v1 — ✅ 冻结（可进入实现准备）

> **冻结。** 受控单轮、规则 Intent、≤1 数据 Tool、fingerprint≠trace、ClaimBinding 只绑定不判真。  
> **下一步：** 按 §9.3 测试矩阵实现并验收；通过后再开 ⑧ 飞书 MVP（接线，不再扩大脑）。

### 9.0 单轮主链（硬约束）

```
User Message
      ↓
Identity                         【②】
      ↓
Permission                       【③】
      ↓
Context（Query Scope + IssueRef）【④】
      ↓
Rule Intent                      【规则优先；不可靠 → refuse】
      ↓
0 或 1 次数据 Tool：
  ask.published | ask.relations_summary | context.list_issues
  （system.help 可单独或拒答文案；不开启第二数据 Tool）
      ↓
Tool Result → EvidenceRef → ClaimBinding【⑤ 绑定，不另判真】
      ↓
Answer + fingerprint + trace
```

**≤1 数据 Tool = 硬约束：** 即使第一次结果空/差，**禁止**临时再调第二个数据 Tool。多步 → 未来 Agent v2/Planner，v1 不得偷偷长成 ReAct。

### 9.1 四条冻结边界

#### 1) Intent = 规则优先，不是 LLM 自由分类

| intent | Tool |
|--------|------|
| `help` | `system.help` |
| `list_issues` | `context.list_issues` |
| `ask_relations` | `ask.relations_summary` |
| `ask_published` | `ask.published` |
| `refuse` | 无数据 Tool |

- 判定：规则 / 关键词 + Context/Permission 状态。  
- **无法可靠判定 → `refuse`**（可附 system 引导），**不**让模型自由选 Tool。  
- 优先级：`refuse` > `help` > `list_issues` > `ask_relations` > `ask_published`。  
- 禁止：LLM 输出「下一步调用 tool_x」的开放计划。

#### 2) ≤1 数据 Tool（硬约束）

一轮消息内，下列三者**至多调用一次合计**：

- `ask.published`  
- `ask.relations_summary`  
- `context.list_issues`  

不得因结果不好链式补打。`system.help` 不计为数据 Tool。

#### 3) fingerprint ≠ trace

| 字段 | 用途 |
|------|------|
| **fingerprint** | 本次回答/检索依据的**事实版本**指纹（含 issue_slug、issue_epoch、query_scope、corpus 版本等） |
| **trace** | 本次请求**路径**：intent、tool、issue、view… |

示例：

```json
{
  "trace": {
    "intent": "ask_relations",
    "tool": "ask.relations_summary",
    "issue": "2026-8-17",
    "view": "chat/编辑部"
  },
  "fingerprint": "..."
}
```

回答「你这个答案哪来的？」→ 至少能还原：哪一期、哪个视角、哪个 Tool。

#### 4) ClaimBinding 只绑定，不再判真

```
Tool Result
  ↓
EvidenceRef
  ↓
ClaimBinding（挂接 + grounded|weak|unsupported 档位）
  ↓
Answer
```

**不是** Agent 自行宣布「这是事实」。Published Evidence + 既有 verify/Claim Check/citation 口径才是依据；unsupported 不上屏为事实句。

### 9.2 编排伪代码（实现提纲）

```
handle(message, envelope):
  identity = resolve_identity(...)
  permission = decide_permission(...)
  context = assemble_context(...)

  if not permission.agent_access:
      return Answer(help/引导, tools_called=0)

  intent = rule_classify_intent(message, context, permission)
  if intent == refuse or uncertain:
      return Answer(refuse/引导, tools_called=0, trace=...)

  tool_id, args = bind_one_tool(intent, context, message)  # 至多一个数据 Tool
  assert count_data_tools(tool_id) <= 1
  if tool_id not in permission.tool_acl:
      return Answer(refuse=denied_acl)

  result = invoke_tool(tool_id, args, context)  # ⑥；禁止直查 DB
  answer = render(result)  # ClaimBinding；unsupported 过滤
  return Answer(text, fingerprint=..., trace=...)
```

### 9.3 第一版必须覆盖的测试（7 类 + 横切）

**场景（7）：**

| # | 场景 | 期望 |
|---|------|------|
| 1 | help | 仅 system.help 或静态说明；无 ask.* |
| 2 | list_issues | 仅 list_issues；只返回 published |
| 3 | 普通 published 问题 | 恰好 1× ask.published；有 fingerprint/trace |
| 4 | relation 问题 | 恰好 1× ask.relations_summary |
| 5 | 未绑定用户 | 无数据 Tool；引导绑定 |
| 6 | team conflict | 无数据 Tool；说明需消歧 |
| 7 | draft/raw 越权请求 | 拒绝；无 draft/raw 内容泄漏 |

**横切：**

| # | 项 |
|---|-----|
| A | ≤1 data Tool（含「空结果也不二次调用」） |
| B | Published-only |
| C | IssueRef 正确（含 locked vs latest） |
| D | Query Scope 正确（群视角不改 Person） |
| E | Evidence 可追溯（ClaimBinding / EvidenceRef） |

全部通过 → ⑦ 打通 → 才开 ⑧。

### 9.4 明确不做

- ReAct / 多 Tool 链 / LLM 自由选 Tool  
- 结果差就偷偷打第二个数据 Tool  
- Agent 侧新 truth score  
- 扩 ⑥ 的 Tool 列表  
- 未测通就上飞书全员  

### 9.5 实现准备清单（不扩架构）

1. 模块边界：`agent_runtime`（编排）只依赖 ②～⑥ 契约接口  
2. `rule_classify_intent` + 单测（上表 7 类）  
3. Tool 注册表仅 4 个；断言无第二数据调用  
4. Answer 强制 `fingerprint` + `trace`  
5. 本地/tmesh 冒烟后再考虑 ⑧  

---

## 10. 实现阶段锁定（非架构讨论）

> **架构 ①～⑦ 已冻结。** 当前不是继续设计，而是：**实现 → 契约测试 → 修实现 → 冻结实现 → ⑧ 接线**。

### 10.1 顺序（硬）

```
实现 ⑦ Agent v1（本地 / HTTP Harness，不依赖飞书）
        ↓
真实 ask.published / ask.relations_summary adapter
        ↓
跑契约测试（含真实 Evidence 冒烟）
        ↓
⭐ Mesh + Agent 全量验收
        ↓
通过 → ⑧ 飞书 MVP：只接线，不扩大脑
```

**禁止在接飞书时加入：** Planner、ReAct、多 Tool 循环、长期 Memory、Multi-Agent、新权限逻辑。

### 10.2 为何先 Harness、后飞书

把问题拆开：

1. Agent 本体契约是否正确？（Identity/Permission/Context/Tool/Answer）  
2. 飞书 OAuth / open_id / 群事件 / 回复是否正确？

直接接飞书会导致失败时无法判断错在哪一层。

### 10.3 Tool 必须自防御

即使 Agent 上游犯错，**Tool 自身**仍须拒绝：

- draft / raw / unpublished  
- 非法 IssueRef  
- 绕过 Permission 的 team scope  
- 未绑定 / conflict 的数据访问  

不能只假设编排层永远正确。

### 10.4 ⑧ 飞书接线形态（测通后）

```
飞书消息 → open_id → Identity → … → Answer → fingerprint+trace → 飞书回复
```

只串已冻结链路，不改大脑。

---

## 11. 冻结纪律

| 层 | 状态 |
|----|------|
| ①～⑥ 架构 | ✅ **冻结** |
| ⑦ Agent v1 | ✅ **实现 + 真实 Adapter 20/20**（不再改架构） |
| ⭐ Mesh + Agent 全量验收 | 🟡 **当前主任务**（见 `docs/MESH_AGENT_FULL_ACCEPTANCE.md`） |
| ⑧ 飞书 MVP | ⛔ **暂缓**（全量通过后只接线） |

---

## 12. 一句话

**⑦ 已过真实数据闸。当前只做 Mesh+Agent 全量验收；⑧ 只接线、不扩大脑。**
