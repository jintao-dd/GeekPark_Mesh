# Colleague Agent v4 · Agentic Enterprise Architecture

> **日期：** 2026-09-11  
> **状态：** **现行产品架构契约**（取代 v2 Stage 1–4 线性计划；v3 Wave 1 为已落地底座，并入本架构第①②⑤层）  
> **一句话：** 用户只认识 **Mesh** 这一个同事；内部按任务复杂度走 Direct Tool → Bounded Plan → Specialist 并行；事实与思想分栏，永不混级。  
> **双轨纪律：** 本架构只动 **Colleague / Hands / Orchestrator**。周报质量轨（Retrieval / Ranking / Claim / Published-only）**继续冻结**，Ask 仍是 Capability，不是第二张嘴。

---

## 0. 废止与继承

| 旧路线 | 处置 |
|--------|------|
| v2 Stage 1 Controller → Stage 2 Persona → Stage 3 Memory → Stage 4 Collaboration | **废止为产品中轴**（文档保留历史） |
| 「绝对不做 ReAct / Planner / Multi-Agent」对 Colleague 轨的禁令 | **废止**；改为 **有笼子的** Bounded ReAct + Planner + Specialist Agents |
| v3「一轮默认一个读工具 / 不做同轮 Ask∥Hands」 | **升级**：读路径允许同轮多 Capability；**写**仍单确认闸；**禁混成一条企业事实**不变 |
| v3 Wave 1 SafetyGate + Colleague Decide + Hands 10 + Identity/UAT + Session 槽 | **全部继承**，作为 v4 底座，不推翻 |

质量轨 / 周报 Pipeline：**仍禁止**开放 ReAct、自由 Planner、解冻 Ranking/Claim。

---

## 1. 用户看到的产品

```
Feishu User
     ↓
Identity / Permission
     ↓
Company Context
     ↓
Session / Memory（短期工作记忆；长期向量 Memory 仍 ⛔）
     ↓
┌────────────────┐
│ Colleague Brain│  ← 唯一人格与最终判断
│  理解用户目标    │
└───────┬────────┘
        ↓
Task Complexity Judge
   /              \
  ↓                ↓
Simple            Complex
  ↓                ↓
Direct Tool     Planner（只出图，不做人格/事实）
                     ↓
              Task Plan / DAG
                     ↓
            Agentic Executor（Bounded ReAct）
              ┌──────┼──────┐
              ↓      ↓      ↓
          Search   Org   Calendar …
              │      │      │
              └──────┼──────┘
                     ↓
            Specialist Agents（能力域工人，不对用户说话）
                     ↓
               Verification
                     ↓
                Synthesis
                     ↓
            一个 Mesh 的回答
```

用户永远听到：

> 「我查了一下，情况是这样的……」

绝不出现：「Search Agent 认为……」。

---

## 2. 五层能力（Colleague Agent v4）

### ① Colleague Brain

- 懂人、懂会话、懂公司边界  
- 产出：用户目标、是否动手、最终语气、FACT/ANALYSIS/OPINION/SUGGESTION 的成文  
- **不做：** 无限 tool loop；不直接拼企业事实；不替代权限系统  

### ② Capability / Tool Layer

| Capability | source_tier | 备注 |
|------------|-------------|------|
| Mesh Ask（Published） | `published` | 企业事实唯一入口 |
| Feishu Hands（search/doc/calendar/im/…） | `feishu_live` | Runtime → Adapter(`native`\|`cli`\|`mcp`) |
| Org / Directory / Members | `feishu_live` 或 org | IdentityPolicy：bot vs UAT |
| Calendar create / 写操作 | `feishu_live` | **必须** prepare→confirm + WRITE 开关 |

### ③ Task Orchestrator

负责：目标 → 拆解 → Capability 选择 → 权限/身份检查 → 串并行 → 预算 → 完成条件。  
**不是第二个大脑**（见 §5）。

### ④ Agent Runtime

- **Bounded ReAct**（有笼子的观察-补步，不是 `while true`）  
- **Specialist Agents**（能力域，不是 Router/Critic 流水线拆人）  
- 并行、校验、失败恢复、部分完成  

### ⑤ Memory / Context

| 层 | 现状 | 规则 |
|----|------|------|
| Session | `pending` / `active_goal` / `last_block` / mentions | TTL 工作记忆 |
| User | Identity + UAT | 个人授权显式引导 |
| Company | Org / Permission | 系统门，不靠 Prompt |
| Task Memory | Plan 执行态（待建） | 随任务结束可归档摘要；**禁止**长期向量 Memory 主路径 |

---

## 3. Complexity Judge（何时上 Planner）

| 档位 | 例 | 路径 |
|------|----|------|
| **Simple** | 「张三是谁？」 | Brain → Org Tool → Answer |
| **Ordinary enterprise** | 「张三最近跟谁聊过？」 | Brain → 单一 Capability（Ask 或 Hands）→ Answer |
| **Medium** | 「…跟谁聊过？哪条值得关注？」 | Brain → Planner（短 DAG）→ Search → Analysis → Answer |
| **Complex** | 「列出我能访问的群 + 成员 + 日历 + 关联周报，哪些值得关注」 | Brain → Planner → 并行 Specialists → Correlation → Verify → Synthesis → Answer |

Judge 原则：

- 默认 **能直连则直连**（省延迟、省预算）  
- 多源、多依赖、要并行、要跨桶关联 → 升 Complex  
- Judge **不**编造公司事实；只选执行档位  

---

## 4. Bounded ReAct（笼子）

**禁止：**

```
while true:
    Think → Act → Observe
```

**允许：**

```
Plan
 → Step
 → Tool
 → Observation
 → 是否需要补一步？（仅在预算内）
 → 完成 / 超时 / 部分完成
```

**预算（初值；Capacity 实测后校准，禁止拍脑袋当永久真理）：**

| 闸 | 初值（设计默认） | 说明 |
|----|------------------|------|
| `plan_steps` | ≤ 8 | DAG 节点上限 |
| `tool_calls` | ≤ 12 | 含补步 |
| `wall_time` | ≤ 60s | 单用户目标 |
| `replans` | ≤ 2 | 观察后允许的有界补边 |
| `writes_per_turn` | ≤ 1 pending | 写仍确认闸 |

超预算 → **诚实部分完成** + 已得 Evidence + 建议下一步；禁止假装做完。

---

## 5. Planner 不是第二个大脑

| Planner **只**做 | Planner **禁止**做 |
|------------------|-------------------|
| 目标澄清（结构化） | 公司事实断言 |
| 步骤 / 依赖 / 并行组 | 最终用户回答 |
| 预算与完成条件 | Persona / 口吻 |
| 失败时有界补边 | 权限放行（权限属系统） |

权限 → Identity / Permission / IdentityPolicy。  
事实 → Grounding（Published Ask + 分桶 Envelope）。  
人格 → Colleague Brain（唯一 Synthesizer 对外）。

---

## 6. Specialist Agents（值得做的 Multi-Agent）

**定义：** 能力域工人，由 Orchestrator 调度；**不对用户说话**。

```
Task Orchestrator
        │
┌───────┼───────────────┐
↓       ↓               ↓
Org    Research        Calendar
Specialist  Specialist  Specialist
│       │               │
└───────┼───────────────┘
        ↓
   Synthesizer（= Colleague Brain）
        ↓
      Mesh
```

| Specialist | 能力域 | 典型工具 |
|------------|--------|----------|
| Org | 组织、成员、角色、群成员 | directory / members / get-user |
| Research | 飞书文档/消息/讨论检索 | hands.search / discuss |
| Calendar | 忙闲、日程、约时间草案 | calendar.list / freebusy / propose |
| Published | 已上线周报 | ask.published |
| Writer（可选） | 确认式写入 | prepare/confirm 闸后 |

**明确不做的拆法：** Router Agent / Search Agent / Answer Agent / Critic Agent 整条拆人对外。  
Verifier 可以是 **Runtime 步骤**（规则 + 轻量 LLM），不是第四张用户可见嘴。

为何值得：专业边界真实存在（组织 / 飞书资源 / 周报 / 时间 / 关系 / 分析）；单超长 Prompt 易 Context 爆炸、Tool 混乱、结构不稳。

---

## 7. 思想放哪里：FACT / ANALYSIS / OPINION / SUGGESTION

任务执行后，Synthesizer **分栏**成文（可同条消息，但语义分栏）：

| 栏 | 含义 | 允许来源 |
|----|------|----------|
| **FACT** | 证据支持的事实 | Published / Hands Envelope / Org；须可指回 |
| **ANALYSIS** | 对事实的关联与解读 | 仅基于已列出的 FACT；须标明跨源 |
| **OPINION** | Mesh 自己的判断 | 模型；**禁止**写成企业事实 |
| **SUGGESTION** | 建议下一步 | 模型；可行动、可拒绝 |

示例（Complex 金丝雀口吻）：

- FACT：这 8 个群里，3 个与你当前工作相关（…依据）。  
- ANALYSIS：其中两个群的近期人员活动与周报项目高度重合。  
- OPINION：我觉得 X 方向最值得继续看。  
- SUGGESTION：如果是我，会先跟进 Y。

这才是「有思想」，且不拿模型想法冒充企业事实。  
与既有 `source_tier` / `truth_level` **叠加**，不替代。

---

## 8. 金丝雀目标句（P0 产品验收）

> 「列出我能访问的群聊和群聊人员详情还有人员日历详情，并梳理一下近期周报和这些人有关联的事情。」

期望内部 DAG（示意）：

```
S1  Group discovery（可访问群）
S2  Member discovery（串在群后）
S3∥ Org lookup（可选补全）
S4∥ Calendar（忙闲/近期；「我的」需 UAT）
S5∥ Published search（周报关联）
S6  Cross-source correlation（只连边）
S7  Verification（空结果/权限/混级检查）
S8  Synthesis → 一张嘴（FACT/ANALYSIS/OPINION/SUGGESTION）
```

一次串起：组织 · User OAuth · Hands · Published · Session · 并行 · 综合判断。  
**用户不必知道**要查什么、怎么 join、怎么并行。

---

## 9. 与现有代码的落点（不推翻 Hands）

| 已有 | v4 位置 |
|------|---------|
| `colleague_v3._decide` | Brain + 简易 Judge（后续显式化 Complexity） |
| Hands Runtime + Adapters | ② Capability |
| `identity_policy` / UAT / auth guide | Identity 门 |
| `feishu.calendar.propose` | Orchestrator 雏形（受控多步） |
| `pending_write` / confirm | Writer 闸 |
| `active_goal` / `last_block` | ⑤ Session |
| `tool_contract` 分桶 | Envelope / Verification 硬门 |

**待建（按序，禁止跳步把用户暴露给半成品 Multi-Agent）：**

1. `TaskPlan` schema + Orchestrator 只读执行器  
2. Complexity Judge 显式档位 + 金丝雀 DAG  
3. Bounded ReAct 预算与补步  
4. Specialist 接口（同 Envelope，先 Org / Research / Calendar / Published）  
5. Synthesis 分栏 FACT/ANALYSIS/OPINION/SUGGESTION  
6. Capacity 校准预算数值  

---

## 10. 测试梯子（必须多次梳理，禁止遗留「半档」）

每一档 **本地 pytest + tmesh 真聊** 过了才开下一档。

| 档 | 用例 | 门禁 |
|----|------|------|
| L0 Simple | 「张三是谁？」 | 无 Planner；Org/Ask 单工具；无周报脚误挂 |
| L1 Ordinary | 「张三最近跟谁聊过？」 | 单源；分桶 footer 正确 |
| L2 Medium | 「…哪条值得关注？」 | 短 Plan；ANALYSIS/OPINION 分栏；无混级 |
| L3 Complex 金丝雀 | 群+人+日历+周报 | 并行可观测；UAT 缺则引导；部分完成诚实；一张嘴 |
| L4 Write | 建日程/文档 | 仍确认闸；与 Plan 共存不偷写 |
| L5 Budget | 人为压预算 | 触发部分完成，不假成功 |
| L6 Identity | bot vs UAT 矩阵 | 与 `FEISHU_PERSONAL_AUTH.md` 一致 |

回归：**不得**回退到关键词意图表、v2 Controller 中轴、无限 ReAct、用户可见多 Agent 话术。

---

## 11. 绝对仍禁止（全产品）

- 无限 `while true` ReAct  
- 用户可见的多 Agent 角色扮演  
- `feishu_live` 写入 `published` / enterprise_fact  
- 无确认 + WRITE 开关的飞书写入  
- 长期向量 Memory 主路径  
- 为 Colleague UX 解冻质量轨 Ranking/Claim  
- Planner 做权限放行或最终人格回答  
- `docker cp` 当发布  

---

## 12. 权威关系

| 文档 | 角色 |
|------|------|
| **本文** | Colleague 轨现行架构 |
| `COLLEAGUE_AGENT_V3.md` | Wave1 历史底座说明（已并入 v4） |
| `COLLEAGUE_FEISHU_HANDS.md` | Hands / Adapter 细节 |
| `FEISHU_PERSONAL_AUTH.md` | 双身份 / UAT |
| `COLLEAGUE_MEMORY_ISOLATION.md` | 分桶隔离 |
| `PROJECT_PROGRESS_*.md` | 进度快照（须指向本文） |
| `AGENT_EVOLUTION.md` / 质量轨文档 | **仅** Grounded Brain / 周报；不在此开开放 Agent |

---

## 13. 成功标准（产品）

用户只说一句复杂目标，Mesh **自己**完成发现、关联与判断，并清楚分开：

- 什么是查到的  
- 什么是分析的  
- 什么是 Mesh 认为的  
- 什么是建议做的  

这才是 **AI 同事**，不是高级搜索框。
