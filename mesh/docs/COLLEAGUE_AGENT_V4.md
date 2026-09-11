# Colleague Agent v4 · Agentic Enterprise Architecture

> **日期：** 2026-09-11  
> **状态：** 能力与分桶合同仍有效；**控制面实现**已切到 [`MESH_SUPERVISOR.md`](./MESH_SUPERVISOR.md)（默认 `MESH_SUPERVISOR=1`）。  
> **一句话：** 用户只认识 **Mesh**；**MeshSupervisor** 分配监督，Workers 执行；事实分桶，永不混级。  
> **双轨纪律：** Ask 内核作为 WorkerPublished 保留；不再把旧 Decide+Planner 双脑当产品中轴。

---

## 0. 废止与继承

| 旧路线 | 处置 |
|--------|------|
| v2 Stage 1 Controller → Stage 2 Persona → Stage 3 Memory → Stage 4 Collaboration | **废止为产品中轴**（文档保留历史） |
| 「绝对不做 ReAct / Planner / Multi-Agent」对 Colleague 轨的禁令 | **废止**；改为 **有笼子的** Bounded ReAct + Planner + Specialist Agents |
| 「Colleague 永不碰 Ontology / LLM Wiki」 | **废止**；改为 **公司理解三角**（§1.5）；**不**等于解冻质量轨 Graph RAG / ES Ask |
| v3「一轮默认一个读工具 / 不做同轮 Ask∥Hands」 | **升级**：读路径允许同轮多 Capability；**写**仍单确认闸；**禁混成一条企业事实**不变 |
| v3 Wave 1 SafetyGate + Colleague Decide + Hands 10 + Identity/UAT + Session 槽 | **全部继承**，作为 v4 底座，不推翻 |
| `MESH_LIGHTWEIGHT_ONTOLOGY.md`（claim↔evidence 语法） | **保留**为质量轨反推稿；与 Colleague **公司 Ontology** 分工见 §1.5 |

质量轨 / 周报 Pipeline：**仍禁止**开放 ReAct、自由 Planner、解冻 Ranking/Claim、用 Wiki/ES **替换** Published Ask。

---

## 1. 用户看到的产品

```
Feishu User
     ↓
Identity / Permission
     ↓
Company Understanding（Ontology · Wiki · Grounding）
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

## 1.5 公司理解三角（Ontology · Wiki · Grounding）

「AI 同事」之前缺的不是更多 Tool，而是 **知道自己在哪家公司当同事**。

```
          Mesh 同事
              │
      ┌───────┼───────┐
      ↓       ↓       ↓
    Ontology  Wiki   Grounding
    懂结构     懂语境   知事实
      │       │       │
      └───────┼───────┘
              ↓
        真正的「懂公司」
   懂结构 + 知道历史语境 + 知道现在发生了什么
```

| 柱 | 回答的问题 | 典型内容 | source_tier / 性质 |
|----|------------|----------|-------------------|
| **Ontology** | 公司里有什么结构？ | 团队、角色、实体类型、关系类型、产品线、权限边界、「谁属于哪」 | **结构知识**（相对慢变）；可来自 Org + 反推 schema + 受控公司本体 |
| **Wiki** | 在这里共事要懂什么语境？ | 黑话、惯例、项目别名、协作习惯、历史背景叙事、「我们怎么称呼这件事」 | **语境知识**（中速）；**LLM Wiki = 派生/整理视图**，不是第二真相源 |
| **Grounding** | 今天/本期到底发生了什么？ | Published 周报事实 +（分桶）Feishu live 近况 | **事实**；企业事实仍 **Published-only** |

### 三者如何配合 Brain

- Ontology → 消歧与选人/选队/选能力（「张三」是哪个、该问 Org 还是 Ask）  
- Wiki → 理解说法与惯例（「上车」「CRS」在本公司指什么）——**不得**单独当成已发生事实  
- Grounding → 断言「发生了什么」；FACT 栏只许挂这里的 Evidence  

成文时：

- FACT ← 仅 Grounding（Published；live 须标明飞书侧）  
- ANALYSIS 可连 Ontology 边 + Wiki 语境说明，但须可指回  
- OPINION / SUGGESTION ← Brain；可引用 Wiki 惯例，但话术不得伪装成周报结论  

### Ontology：两层，勿混

| 层 | 文档/现状 | 用途 |
|----|-----------|------|
| **Claim Ontology（轻量）** | `MESH_LIGHTWEIGHT_ONTOLOGY.md` | 质量轨 claim↔evidence **语法**；冻结，不扩建 Graph |
| **Company Ontology（Colleague）** | 本架构新建产品柱 | 让 Brain/Judge/Specialists **懂组织与对象世界**；从 Org + Published 实体/团队 **反推+受控维护**，不是先画理想图谱再改库 |

Company Ontology **允许**成为 Orchestrator / Org Specialist 的只读上下文；**禁止**因此解冻 Ranking/Recall，或上 Neo4j 当 Ask 主路径。

### Wiki：值得做，但要笼子

**值得：** 没有 Wiki，同事只会「检索命中」，不会「懂我们怎么说话、过去怎么协作」。  

**笼子：**

| 允许 | 禁止 |
|------|------|
| 从 Published / 飞书公开材料 **派生** 的公司语境页 | Wiki 条目 **冒充** enterprise_fact |
| Brain / Research Specialist **只读**引用 | 无限自动写入、无人审的 Wiki 自生长 |
| 显式 `source_tier=wiki_context`（语境） | 用 Wiki/ES **替换** Published Ask Retrieval |
| 人可审计的维护入口（后置） | 「通用知识库」吞掉周报真相 |

飞书 `wiki:` 搜索能力属 **Hands Capability**（live 材料）；**LLM Wiki** 属 **Company Understanding** 派生层——两者都要，但标签不同。

### Grounding：已经有，位置要写清

Grounding = Published Ask（+ Claim/Evidence 纪律）∪ 分桶后的 Hands live。  
它解决 **知事实**；不解决 **懂结构/懂语境**——所以必须与 Ontology、Wiki 并列，而不是互相替代。

### 与五层能力的关系

公司理解三角 **横贯** ① Brain 与 ⑤ Memory/Context，并被 ③ Orchestrator 当先验：

- Judge：缺结构线索 → 先碰 Ontology/Org；缺事实 → Grounding；说法含糊 → Wiki 消歧  
- Complex 金丝雀：群/人/日历 = 结构+live；周报关联 = Grounding；「值得关注」= ANALYSIS/OPINION，可借 Wiki 项目语境  

---

## 2. 五层能力（Colleague Agent v4）

### ① Colleague Brain

- 懂人、懂会话、**懂公司（消费 Ontology/Wiki/Grounding）**  
- 产出：用户目标、是否动手、最终语气、FACT/ANALYSIS/OPINION/SUGGESTION 的成文  
- **不做：** 无限 tool loop；不直接拼企业事实；不替代权限系统  

### ② Capability / Tool Layer

| Capability | source_tier | 备注 |
|------------|-------------|------|
| Mesh Ask（Published） | `published` | **Grounding** 企业事实入口 |
| Feishu Hands（search/doc/calendar/im/wiki…） | `feishu_live` | Runtime → Adapter(`native`\|`cli`\|`mcp`) |
| Org / Directory / Members | `feishu_live` 或 org | IdentityPolicy；喂 **Company Ontology** |
| Company Wiki（派生视图） | `wiki_context` | 语境；不得当 enterprise_fact |
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
| Company | **Ontology + Wiki + Permission/Org** | 结构+语境；系统门不靠 Prompt 放权 |
| Grounding cache | Ask/Hands Envelope（短） | 事实片段可进 Task Memory，须带 tier |
| Task Memory | Plan 执行态（待建） | 随任务结束可归档摘要；**禁止**长期向量 Memory 主路径 |

---

## 3. Complexity / 何时上 Planner

产品中轴：**Decide（要不要动手）→ LLM Planner（用什么工具、怎么拆）→ Bounded 执行**。

| 档位 | 例 | 路径 |
|------|----|------|
| **Simple** | 「哈哈今天忙死了」 | Decide=`speak` → Brain 直接回 |
| **Ordinary enterprise** | 「张三最近跟谁聊过？」 | Decide=`work` → Planner 出 1 步 Ask/Hands → Answer |
| **Medium** | 「…跟谁聊过？哪条值得关注？」 | Planner 短 DAG → Search → Analysis → Answer |
| **Complex** | 「列出我能访问的群 + 成员 + 日历 + 关联周报」 | Planner 多步并行 → Specialists → Synthesis → Answer |

原则：

- **禁止**用关键词/正则穷举用户话术当产品主路径；任意措辞由模型在工具白名单内判断  
- 正则 Judge 仅作 trace / 极端兜底痕迹，**不**决定工具图  
- Planner **不**编造公司事实；只出步骤图与 band  
- 默认能直连则短图（1 步也是合法 plan）  

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

实现：`plan_with_llm()`（工具白名单 + 预算 + 依赖规范化）。`build_plan()` 仅 LLM 失败时的最小模板，不得伪装「已理解全部话术」。

| Planner **只**做 | Planner **禁止**做 |
|------------------|-------------------|
| 目标澄清（结构化） | 公司事实断言 |
| 步骤 / 依赖 / 并行组 | 最终用户回答 |
| 预算与完成条件 | Persona / 口吻 |
| 失败时有界补边 / Decide 单工具提示 | 权限放行（权限属系统） |
| 按用户原意规划（任意措辞） | 关键词意图表 / 固定 canary DAG 当主路径 |

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
| Org | 组织、成员、角色、群成员；**消费 Company Ontology** | directory / members / get-user |
| Research | 飞书文档/消息/讨论；可借 **Wiki 消歧** | hands.search / discuss |
| Calendar | 忙闲、日程、约时间草案 | calendar.list / freebusy / propose |
| Published | 已上线周报（**Grounding**） | ask.published |
| Context（可选） | Company Wiki 只读 | wiki_context 派生页 |
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

## 9. 两维分立：Architecture ≠ Execution Complexity

**你的思路对。** 把验收档位排成 L0→L8 再写成「做完 L2 才能做 L3」，等于把 v2 Stage 又偷回来了。

| 维度 | 是什么 | 不是什么 |
|------|--------|----------|
| **Architecture** | 固定基建栈（§9.1） | 不是项目阶段门 |
| **Execution Complexity** | 运行时 Judge 对**这一句用户目标**分档 | 不是「先做完 Simple 产品再开 Complex」 |

```
Architecture（固定，始终在）
  Identity · Ontology · Wiki · Grounding · Session
  · Brain · Orchestrator · Hands/Ask · Specialists · Synthesis
                    │
                    │ 同一套基建
                    ▼
Execution Complexity（按目标选路径）
  Simple → Direct Tool
  Ordinary → 单 Capability
  Medium → 短 Plan
  Complex → 并行 DAG + Specialists
```

因此：**Complex 金丝雀可以直接走 Complex 路径**，底下用的仍是同一套 Ontology / Wiki / Hands / Session / Planner——不是另开一条「L3 专用架构」。

旧 §10「C0→…→L6 过了才开下一档」若被读成产品路线图 — **作废该读法**。

### 9.1 运行时栈序（Architecture · 固定）

```
Identity / Permission
        ↓
Company Ontology          ← 懂结构（先验）
        ↓
Company Wiki              ← 懂语境（先验）
        ↓
Grounding（Published + 分桶 live）  ← 知事实（按需）
        ↓
Session / Context
        ↓
Colleague Brain
        ↓
Orchestrator / Bounded ReAct / Specialists
        ↓
Synthesis（一张嘴）
```

**判定：** 缺公司理解层 = 通用 Agent 穿 GeekPark 外套。  
基建要齐；**不**等于必须按 Simple→Complex 的产品阶段串行发版。

### 9.2 已有代码落点

| 已有 | Architecture 位置 |
|------|-------------------|
| Identity / Permission / UAT | 栈顶门 |
| Org directory / members / teams | Company Ontology v0 原料 |
| Published Ask + Claim/Evidence | Grounding |
| Session 槽 | Session / Context |
| `colleague_v3._decide` | Brain（待显式消费先验 + Judge） |
| Hands Runtime + Adapters | Capability |
| `feishu.calendar.propose` | Orchestrator 雏形 |
| `tool_contract` 分桶 | Envelope / Verification |

### 9.3 基建交付（横切，不是 Stage）

下列是 **统一基础设施的缺口清单**，可并行推进；**禁止**再解释成「做完 5 才能做 7」的 Stage 门。  
唯一硬依赖：Specialist/对外 Complex **对外宣称同事**前，Ontology/Wiki **注入路径**必须存在（可先是 v0 种子，不可是空壳）。

| 基建面 | v0 完成定义 |
|--------|-------------|
| Company Ontology | 只读结构包注入 Context；反推自 Org+Published |
| Company Wiki | `wiki_context` 种子页注入；禁进 FACT |
| Session / Context | 先验 + 会话槽同一对象 |
| Brain | 消费先验；无先验不装懂 |
| Orchestrator / TaskPlan | 只读 DAG；写仍 confirm |
| Complexity Judge | 按**目标**选 Simple…Complex（§3） |
| Bounded ReAct + Specialists | 预算内；不对用户说话 |
| Synthesis 分栏 | FACT/ANALYSIS/OPINION/SUGGESTION |
| Capacity | 实测校准预算，不拍脑袋 |

Company Understanding **在架构上前置**（先验）；工具选择与任务拆解由 **LLM Planner** 完成（`plan_with_llm`），禁止用关键词穷举用户话术；正则 Judge 仅作痕迹/兜底，不是产品中轴。

---

## 10. Execution Complexity（运行时分档）+ 验收用例

### 10.1 运行时分档（只描述目标有多难）

与 §3 一致；**不是**发版阶段编号：

| 档 | 用户目标形态 | 执行路径（同一套 Architecture） |
|----|--------------|----------------------------------|
| **Simple** | 「张三是谁？」 | Brain → 直连 Org/Ontology |
| **Ordinary** | 「张三最近跟谁聊过？」 | Brain → 单 Capability（Ask 或 Hands） |
| **Medium** | 「…哪条值得关注？」 | 短 Plan → Search → Analysis → 分栏成文 |
| **Complex** | 群+人+日历+周报金丝雀 | 并行 DAG → Specialists → Correlate → Verify → Synthesis |

写操作、预算击穿、身份矩阵 = **横切验收场景**（挂在 Architecture 上测），**不要**再编号成 L4/L5/L6 假装下一 Stage。

### 10.2 验收用例（可并行、可直打 Complex）

| 场景 | 验证什么 | 说明 |
|------|----------|------|
| Ontology 消歧 | 结构先验 | Architecture 面 |
| Wiki 黑话 | 语境先验；不进 FACT | Architecture 面 |
| Brain±先验 | 无先验不装懂 | Architecture 面 |
| Simple / Ordinary / Medium / Complex | Judge 选对路径 + 分桶 | **Execution** 面；Complex **可直接测** |
| Write 确认闸 | 与 Plan 共存不偷写 | 横切 |
| Budget 部分完成 | 不假成功 | 横切 |
| Identity bot/UAT | 与个人授权一致 | 横切 |

门禁原则：

- 某一 Execution 档失败 → 修路径或基建缺口，**不是**「退回上一 Stage 重做产品」  
- Complex 失败而 Simple 仍绿 → 说明并行/关联/先验不够，不是「没资格做 Complex」  
- 禁止用 L0–L8 叙事替代 Architecture  

回归：**不得**回退关键词意图表、v2 Controller、无限 ReAct、用户可见多 Agent、Wiki 冒充 Grounding、把 Complexity 档重新当成 Stage 1–4。

---

## 11. 绝对仍禁止（全产品）

- 无限 `while true` ReAct  
- 用户可见的多 Agent 角色扮演  
- `feishu_live` / **Wiki 语境** 写入 `published` / enterprise_fact  
- 用 Wiki / ES / Neo4j **替换** Published Ask 作为企业事实主路径  
- 无确认 + WRITE 开关的飞书写入  
- 长期向量 Memory 主路径  
- 为 Colleague UX 解冻质量轨 Ranking/Claim  
- Planner 做权限放行或最终人格回答  
- `docker cp` 当发布  

---

## 12. 权威关系

| 文档 | 角色 |
|------|------|
| **本文** | Colleague 轨现行架构（含公司理解三角） |
| `MESH_LIGHTWEIGHT_ONTOLOGY.md` | 质量轨 Claim Ontology（语法）；≠ Company Ontology |
| `COLLEAGUE_AGENT_V3.md` | Wave1 历史底座说明（已并入 v4） |
| `COLLEAGUE_FEISHU_HANDS.md` | Hands / Adapter 细节 |
| `FEISHU_PERSONAL_AUTH.md` | 双身份 / UAT |
| `COLLEAGUE_MEMORY_ISOLATION.md` | 分桶隔离 |
| `PROJECT_PROGRESS_*.md` | 进度快照（须指向本文） |
| `AGENT_EVOLUTION.md` / 质量轨文档 | **仅** Grounded Brain / 周报；Ask 不得被 Wiki 替换 |
| `COLLEAGUE_V4_ACCEPTANCE_8Q.md` | 八问产品验收（非单 case） |

### 实现锚点（代码）

| 模块 | 路径 |
|------|------|
| Ontology | `app/agent/company_ontology.py` |
| Wiki | `app/agent/company_wiki.py` |
| Context 组装 | `app/agent/company_context.py` |
| Orchestrator | `app/agent/orchestrator.py` |
| Brain 接线 | `colleague_v3.handle`（先验注入 + medium/complex 编排） |
| 开关 | `MESH_COLLEAGUE_ORCHESTRATOR`（默认开） |
| 测试 | `tests/test_colleague_v4.py` |

---

## 13. 成功标准（产品）

用户只说一句复杂目标，Mesh **自己**完成发现、关联与判断，并清楚分开：

- 什么是查到的（Grounding）  
- 结构上它属于哪（Ontology）  
- 在我们公司这句话什么意思（Wiki）  
- 什么是分析的 / Mesh 认为的 / 建议做的  

**懂公司 = Ontology（结构）+ Wiki（语境）+ Grounding（事实）**；再叠加 Orchestrator，才是 **AI 同事**，不是高级搜索框。
