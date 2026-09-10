# Colleague × Feishu Hands · 接入计划

> **日期：** 2026-09-10  
> **状态：** **10 项能力已接入代码**（默认 Hands 关；写另需 WRITE=1；联调可用 `MESH_FEISHU_HANDS_BACKEND=mock`）  
> **目标态：** 飞书 → Colleague Brain →（Mesh Ask | Feishu MCP/CLI | Org）→ 一张嘴  
> **原则：** 官方当手脚；不自研全套飞书 Tool；**绝不混级**；**绝不越权**。  
>
> 十项：search(doc/message/wiki/folder/calendar) · doc.get · calendar.list · discuss.summary ·  
> speak 成文 · doc.create / im.send / calendar.create（确认式写）  

---

## 0. 目标态

```
飞书用户
   ↓
Colleague Brain（身份 / 权限 / 怎么说 / 要不要动手）
   ├─ Mesh Ask       → 已发布周报事实（Evidence）
   ├─ Feishu MCP     → 运行时飞书手脚（官方）
   ├─ Feishu CLI     → 开发 / 运维 / 调试 / 脚本（辅助）
   └─ Org Context    → 谁 / 哪队（Architecture v1 已有）
   ↓
统一同事口吻（唯一成文者）
```

**禁止：** 自研全套 Feishu OpenAPI 壳；Controller / 多 Intent / 多 Agent；ReAct 长环；飞书 live 伪装 Published；无确认写操作。

---

## 1. 事实等级（死契约）

Brain 同时拿到多源时，**禁止揉成一个事实池**。

| 来源 | source_tier | truth_level | 话术示例 |
|------|-------------|-------------|---------|
| 已上线周报 / Ask | `published` | `enterprise_fact` | 「周报里记录的是…」 |
| 飞书文档/消息等 | `feishu_live` | `live_context` | 「飞书最近的讨论则…」 |
| 模型常识/观点 | `model` | `general_knowledge` | 「我觉得…（看法）」 |

合成目标句式：

> 「周报里记录的是 X；飞书最近的讨论则更多集中在 Y。」

代码锚点：`app/agent/tool_contract.py`（`SourceTier` / `TruthLevel`）。

---

## 2. Tool Contract（每个工具必须具备）

Brain **不**直接知道 MCP 细节；只看见统一 Contract：

```
Tool
├── name
├── description
├── input_schema
├── permission_scope
├── timeout
├── max_results
├── source_tier          # published | feishu_live | model
├── truth_level          # enterprise_fact | live_context | general_knowledge
├── output_schema
├── side_effect          # none | write
└── confirmation_required  # bool
```

示例：`feishu.search`

| 字段 | 值 |
|------|-----|
| side_effect | `none` |
| permission_scope | `doc.read`（首期） |
| source_tier | `feishu_live` |
| truth_level | `live_context` |
| confirmation_required | `false` |
| timeout | 建议 ≤8s |
| max_results | 建议 ≤8 |

写类工具：`side_effect=write`，`confirmation_required=true`，且受 `MESH_FEISHU_HANDS_WRITE` 总开关约束。

---

## 3. Feishu Search 工具面（防碎片）

**不要**长出 `feishu.search_docs` / `feishu.search_messages` 一堆名字。

从第一天起统一为：

```
feishu.search
  input:
    query: string
    resource_type: doc | message | group | ...
```

| 阶段 | 开放的 resource_type |
|------|----------------------|
| Phase 2～3 | **仅 `doc`** |
| 更后 | 再开 `message` / `group`… |

适配层把官方 MCP/CLI 结果 normalize 成统一 `output_schema`（title / snippet / url / permission_ok / source_tier）。

---

## 4. 执行模型：默认串行，明确可并行

| 模式 | 何时 |
|------|------|
| **默认串行** | 权限未通、首版正确性优先、工具有依赖 |
| **明确可并行** | 工具互相独立（如 Ask ∥ Feishu Search） |

例：「结合这周周报和飞书文档看张三在忙什么」

```
Mesh Ask ─────┐
              ├──→ Brain 合成
Feishu Search ┘   （Phase 3+ 允许并行）
```

Phase 2 首版 `feishu.search(doc)` 可先串行，把正确性与权限跑通；Task/Tool Runtime 设计预留并行（已有 Cards 并行收益先例，勿写死「永远串行」）。

超时：单工具超时则**跳过该源**，不编造；Brain 自然说「飞书这边这轮没查到 / 超时了」。

---

## 5. 写操作分级（Phase 4）

| 动作 | 是否确认 |
|------|----------|
| 读（search 等） | 无需确认 |
| 生成（Brain 成文，未写入飞书） | 无需确认 |
| **准备写入** | **需要确认** |
| **真正写入** | **需要确认 + 明确目标** |

示例：

> 用户：帮我整理成飞书文档  
> Mesh：我已经整理好了，准备创建到你的飞书空间。要创建吗？  
> （确认后才 write）

`MESH_FEISHU_HANDS_WRITE=0` 时：仍可「写作/整理」，**禁止**真正写入。

---

## 6. Phase 路线（可直接执行）

### Phase 0 · Colleague Brain 收口

**验收（业务通过项）：**

- JSON / 协议不泄漏  
- 写稿（字数够、直接交付）  
- 闲聊  
- 情绪 / 反馈  
- **已有 Mesh Ask 的企业事实**（如已上线周报问法）

**不算 Phase 0 业务通过项：**

- 「张三最近跟谁聊过？」作为「飞书实时沟通」验证 → **归 Phase 3 Hands E2E**  
- Phase 0 若测此句：只允许验证「意识到要走 Ask/工具、不胡编」；**不得**当飞书 Hands 已通过

### Phase 1 · MCP / CLI 选型 + Contract

**选型结论（已定）：**

| 项 | 结论 |
|----|------|
| 运行时 | **MCP 为主** — Colleague Brain 的工具入口 |
| 辅助 | **CLI 为辅** — 开发 / 运维 / 调试 / 脚本 |
| 适配 | Mesh 极薄 Tool Adapter；**不**重实现 Feishu OpenAPI |

原因：Brain 是 Agent，运行时适合 MCP 工具语义；CLI 更适合人/Shell/Coding Agent。

产出：Contract 落地（`tool_contract.py`）、scope 清单、开关设计。

### Phase 2 · Feishu Hands 骨架

- `feishu_hands/` 薄适配  
- 只开放 `feishu.search` + `resource_type=doc`  
- 过 Identity / Permission；失败不编造；空结果自然「没查到」

### Phase 3 · Mesh Ask + Org + Feishu

场景：**「张三在忙什么 / 张三最近跟谁聊过」** Hands E2E  

1. Org 解析人  
2. Ask（published）  
3. Feishu Search doc（live_context）  
4. 一张嘴合成，强制分 tier  

可选并行 Ask ∥ Feishu。消息检索仍未开放 unless 显式开 `resource_type=message`。

### Phase 4 · 确认式 Write

准备写入确认 → 真正写入确认+目标；总开关默认 off。

### Phase 5 · 更多 Skills + Task Runtime

按使用量加 `resource_type` / 写能力；并行调度生产化；观测。

---

## 7. 时间盒

| 周 | 内容 |
|----|------|
| Week 0 | Phase 0 盲测收口 |
| Week 1 | Phase 1 Contract + 选型固化；Phase 2 骨架 |
| Week 2 | Phase 2 `feishu.search(doc)` 上 tmesh |
| Week 3 | Phase 3 E2E |
| Week 4+ | Phase 4 确认式 Write |

不可跳过 Phase 0/1 直接上生产写操作。

---

## 8. 成功标准（硬指标优先）

**体验**

- 飞书里像同事  
- 能引已上线周报  
- 能带飞书文档/讨论且标明来源  

**硬指标（必须）**

- **不越权：** 不因部门身份自动获得未授权 Feishu 内容  
- **不混级：** 不把 `feishu_live` 伪装成 `published` / enterprise_fact  
- **不编造：** Tool 失败时不编造结果  
- **空结果诚实：** Tool 返回空时自然「没查到」  
- **写必确认：** 无明确确认绝不执行写操作  

---

## 9. 风险护栏

| 风险 | 护栏 |
|------|------|
| 混级 | source_tier + truth_level + 合成话术 |
| 越权 | Permission 前置；最小 scope |
| 工具碎片 | 统一 `feishu.search` + resource_type |
| 又变 Controller | 禁止 Feishu Agent；只扩 Brain 工具表 |
| 永远串行拖慢 | 默认可串行；独立工具可并行 |
| 官方变更 | Adapter + 版本钉死 + Contract 契约测 |

---

## 10. 现在立刻做什么

1. Phase 0 真人盲测（**不含** Hands 意义的「张三跟谁聊过」业务通过）  
2. Phase 0 过关 → Phase 1/2（MCP + `feishu.search(doc)`）  
3. Phase 3 再测「张三在忙什么」全链路  

---

## 11. 选型结论（已填）

- **运行时：** MCP 为主，CLI 为辅  
- **首批能力：** `feishu.search` / `resource_type=doc`  
- **首批 scope：** 最小 `doc.read`（开通清单 Phase 1 实装时核对开放平台）  
- **与现有 bot 凭证：** Hands 复用/对齐现有应用凭证；user-scoped 读另开，不得默认抬权  
