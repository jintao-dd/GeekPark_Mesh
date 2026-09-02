# 关系决策（第一阶段 · 仅决策，禁止叙事）

你会收到带 `candidate_id` 的 **relation_candidates**。对每个候选 **必须** 输出一条决策。

## 禁止输出
- **禁止** title、body、details、sources、teams、weak、evidence
- **禁止** 自创 candidate_id 或决策候选列表外的关系

## 每条决策字段
| 字段 | 说明 |
|------|------|
| `candidate_id` | 必须与输入一致 |
| `decision` | `keep` 或 `skip` |
| `label` | `keep` 时从标签库 **17 选 1**；`skip` 时可为空字符串 |
| `decision_tier` | `keep` 时必填：`strong`（明确事链，可上读者页）· `parallel`（同赛道/并行，留 draft）· `watch`（弱观察/单边路由，留 draft）；`skip` 时填 `skip` |
| `relation_type` | `keep` 时必填：`event_chain` · `parallel_tracks` · `info_complement` · `one_sided` · `external_watch` · `overseas_link` · `needs_review` |
| `reason` | 一句话：为何 keep/skip（须与 label、relation_type 一致） |
| `evidence_refs` | `keep` 时：选用的 **item_id** 列表（必须来自该候选 `team_facts[].item_ids`）；`skip` 时：`[]` |

## 决策标准（先选标签，再决定 skip）

产品定义：跨团队关系 = **被不止一个团队碰到，或一个团队碰到、另一个明显用得上**。

**默认倾向 keep 并选贴切标签**；只有「无跨团队信号」才 skip。

### 分类顺序（keep 时）
1. **事链 / 互补** → `已联动` / `同一件事，两个部门各知一半` / `一方接触了，另一方正在接触` → `decision_tier: strong`
2. **同赛道 / 并行 / 不同触点** → `同一赛道，各自在做` / `同一公司，不同触点` / `两个部门各有判断` / `采访对象也是客户` / `已公开报道，内部也在用` / `中英文站同周各自成稿` → `decision_tier: parallel`  
   （话题相近但**不是**同一事件 → **keep parallel**，**不要 skip**）
3. **单边 / 路由 / 海外** → `一方接触，另一方用得上` / `一方有需求，另一方尚未接触` / `海外接触，国内可能承接` / `海外新发现，国内尚未接触` / `一方报道了，另一方在接触`；或候选 `teams` 含 `→ 团队` → `decision_tier: watch`
4. **弱观察** → `外部在热聊，我们还没碰` / `两处记录待核对` / `已排期，内容侧待安排` → `decision_tier: watch`
5. **skip 仅当**：纯噪声（同名误命中、无任何跨团队价值），或候选与 snippets 完全对不上

### evidence_refs
- **双团队 / parallel / info_complement / strong**：尽量覆盖 **≥2 个不同团队** 的 item
- **单边 / 路由 / 海外**（含 `→ 团队`）：**≥1 条** 来自已有记录团队即可

### 注意
- label **须从标签库原文 17 选 1**（写「同一赛道，各自在做」，不要写「同一条赛道」）
- parallel/watch 进 draft backlog 同样有价值；**不要为了「不够 strong」而 skip**
- `keep` 时必须给 `label` + `evidence_refs`

## relation_type / decision_tier 与 label（keep 时）
- `已联动` → `event_chain` + `strong`；reason 点明同一事件/活动/对象
- `同一赛道，各自在做` / `同一公司，不同触点` → `parallel_tracks` + `parallel`；reason 可写「非同一事件、各自推进」
- `同一件事，两个部门各知一半` → `info_complement` + `strong`
- `一方接触，另一方用得上` / 海外类 → `one_sided` 或 `overseas_link` + `watch`
- `skip` 时 relation_type 可为空，`decision_tier` 为 `skip`

## 标签库（不得自创）
两处记录待核对 · 一方有需求，另一方尚未接触 · 一方接触了，另一方正在接触 · 已公开报道，内部也在用 · 同一件事，两个部门各知一半 · 采访对象也是客户 · 两个部门各有判断 · 已联动 · 一方接触，另一方用得上 · 外部在热聊，我们还没碰 · 海外接触，国内可能承接 · 海外新发现，国内尚未接触 · 中英文站同周各自成稿 · 一方报道了，另一方在接触 · 同一赛道，各自在做 · 同一公司，不同触点 · 已排期，内容侧待安排
