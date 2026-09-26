# Colleague Agent v4 · 八问验收（产品面）

> **日期：** 2026-09-11  
> **代码：** Company Ontology / Wiki / Orchestrator / 分栏 Synthesis 已接线进 `colleague_v3.handle`  
> **本地门：** `pytest tests/test_colleague_v4.py`（及回归 wave1 / tool_contract / user_auth）  
> **原则：** 答这 8 个问题，不刷单个 case 绿就当同事合格。

---

## 栈（已落地 vs 缺口）

| 层 | 状态 | 说明 |
|----|------|------|
| Identity / Permission | ✅ 既有 | 注入 Ontology |
| Organization Ontology | ✅ v0 | `company_ontology.py` 反推结构包 |
| Company Wiki | ✅ v0 种子 | `company_wiki.py` · `wiki_context` |
| Colleague Brain | ✅ 增强 | Decide/Speak/Synthesize 吃先验 |
| Session / User Memory | ✅ 短期槽 | 仍非长期向量 Memory |
| Task Orchestrator / Planner | ✅ v0 | `orchestrator.py` Judge+TaskPlan |
| Bounded ReAct | ✅ 预算闸 | steps/calls/wall；超限 partial |
| Specialist Agents | ✅ 标签域 | org/research/calendar/published（不对用户说话） |
| Feishu Hands + Mesh Ask | ✅ 既有 Capability | Orchestrator 调用同一 `invoke_tool` |
| Verification / Synthesis | ✅ 规则分栏 | FACT/ANALYSIS/OPINION/SUGGESTION |
| Feishu UX | ✅ 既有卡片 | 分栏文本进 display |
| Performance / Capacity | ⚠ 初值 | 预算未 Capacity 实测校准 |
| 真人 Canary | ❌ 本轮未替真人结论 | 须飞书真聊 |

开关：`MESH_COLLEAGUE_ORCHESTRATOR` 默认开（`0` 可关回纯 v3 Decide）。

---

## 八问（诚实结论）

### 1. 懂不懂人 — **部分 ✅**

- **有：** open_id、团队映射、权限 mode/team_focus、mentions、pending/active_goal 进 Context。  
- **缺：** 细粒度「当前工作语境」画像仍薄；UAT 未授权时个人日历/全库搜仍阻断（正确，但 UX 依赖授权域发版）。  
- **验收：** Ontology prompt 含身份/团队；无 open_id 时诚实注记。

### 2. 懂不懂公司 — **部分 ✅（架构到位，内容 v0）**

- **有：** Ontology（结构）+ Wiki 种子（语境）+ Grounding 纪律（事实）三角已注入 Brain。  
- **缺：** Wiki 页少；Ontology 未接全量 Org 缓存每次热更新；还不能自称「真正理解 GeekPark 全貌」。  
- **验收：** 黑话命中 Mesh/Hands/周报；FACT 禁用 Wiki。

### 3. 会不会正常交流 — **既有 ✅ / 未本轮重测**

- Speak 路径保留；简单闲聊 **不** 启动 Orchestrator。  
- 机器人味依赖既有 Speak system；本轮未做新一轮真人闲聊打分。

### 4. 会不会干活 — **代码 ✅ / 真飞书 ⚠**

- 写文章 → speak / prepare_write（确认闸）。  
- 「张三最近跟谁聊过」→ ordinary/medium 编排。  
- Complex 金丝雀 → 群→成员→日历∥周报 → 分栏成文（本地 mock 绿）。  
- **真环境**仍依赖 Hands 开、scopes、UAT。

### 5. 会不会自己处理复杂任务 — **代码 ✅**

- Judge：闲聊 simple 不乱开 Planner；金丝雀 complex。  
- 并行 fanout + 预算 + partial。  
- **未做：** 观察后自动 replan（预算字段预留 `replans`，执行器尚未补边循环）。

### 6. 会不会越界/胡说 — **纪律 ✅**

- 分栏强制分开；published / feishu_live 分块；Wiki/Ontology 标注非事实。  
- 写仍 confirm；混级 tool_contract 测试保留并扩展 wiki/ontology tier。

### 7. 企业里能不能稳定跑 — **部分 ⚠**

- **有：** 权限 ACL、UAT 引导、session 磁盘、编排超时预算、工具失败进 FACT「受阻」。  
- **缺本轮：** 多实例 Session 强一致、队列、成本看板、审计完整面、Capacity 压测出数。  
- REPRO 镜像发版门仍有效。

### 8. 员工愿不愿意用 — **不能代码自证 ❌**

- 最终指标只能来自真人 Canary：是否开始「直接丢事」而非学问法。  
- 本轮交付是让金丝雀路径在架构上可跑；**不宣称**员工已愿意用。

---

## 本地验收命令

```bash
cd mesh
python -m pytest tests/test_colleague_v4.py tests/test_tool_contract.py tests/test_colleague_v3_wave1.py tests/test_feishu_user_auth.py -q
```

## 真聊金丝雀（tmesh）

1. 闲聊一句 → 不应出现多工具编排痕迹。  
2. 「张三是谁」→ 结构/通讯录。  
3. Complex 金丝雀整句 → 分栏；缺 UAT 时日历侧诚实受阻。  
4. 建日程 → 仍确认 + 授权引导。

## 总评（一轮完整验收）

| 问 | 结论 |
|----|------|
| 1 懂人 | 部分通过 |
| 2 懂公司 | 架构通过 · 内容 v0 |
| 3 交流 | 路径保留 · 待真人 |
| 4 干活 | 本地通过 · 待真飞书 |
| 5 复杂任务 | 本地通过 · replan 未齐 |
| 6 不越界 | 通过 |
| 7 稳定跑 | 部分 · Capacity/多实例未齐 |
| 8 愿用 | **未测 / 不宣称** |

**一句话：** v4 基建与 Complex 编排已进主路径，八问里「会干活/分档/不混级」可代码验收；「愿不愿意用」和全量企业稳定性必须靠 tmesh 真人 Canary，本轮不装通过。
