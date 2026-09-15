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

## 决策标准（质量优先：默认 skip，说不清关系就不成卡）

产品定义：跨团队关系 = **至少两个团队都有可引用事实**，且事实落在**同一公司/人/项目锚点**上。

**默认倾向 skip**。只有能一句话说清「为何是一条关系」才 keep。宁缺毋滥。

### 分类顺序（keep 时）
1. **事链 / 互补** → `已联动` / `同一件事，两个部门各知一半` / `一方接触了，另一方正在接触` → `decision_tier: strong`
2. **同赛道 / 并行 / 不同触点** → `同一赛道，各自在做` / `同一公司，不同触点` / `两个部门各有判断` / `采访对象也是客户` / `已公开报道，内部也在用` / `中英文站同周各自成稿` → `decision_tier: parallel`  
   （话题相近但**不是**同一事件 → 仅当两侧同锚点且各有实质动作时 keep parallel）
3. **必须 skip（不成卡）**：
   - **无共享锚点**：两侧提到的不是同一公司/人/项目（拼盘标题、各说各的）→ **skip**
   - **纯实体共现**：标题「A · B」但 B 只在一侧出现，或只是名单/数据库碰巧同现 → **skip**
   - **假并行凑数**：一侧是播放量/阅读量/发文统计等报表，与另一侧选题/客户无同一对象 → **skip**
   - **单边 / 海外 / 虚线路由**：只有一队有记录、另一侧仅是「→ 建议承接」→ **skip**
   - **弱观察无双边事实**：`外部在热聊，我们还没碰` 等若只有单侧 snippets → skip
   - 纯噪声（同名误命中、候选与 snippets 完全对不上）→ skip

### evidence_refs
- **keep 一律**：尽量覆盖 **≥2 个不同团队** 的 item；系统 Gate 也会拦截单团队 / 无共享锚点 keep
- 不要为「海外线索 / 单边路由 / 报表凑数」凑 keep

### 注意
- label **须从标签库原文 17 选 1**（写「同一赛道，各自在做」，不要写「同一条赛道」）
- **「同一公司，不同触点」**：两侧必须是**同一公司主体**，禁止把两家无关公司拼进一张卡
- **「已联动」**：必须有同一事项上的连续/对接动作，禁止「两边都提过同一名字」就标已联动
- **不要**再用 watch 档把单边卡塞进草稿；单边不成卡
- `keep` 时必须给 `label` + `evidence_refs`

## relation_type / decision_tier 与 label（keep 时）
- `已联动` → `event_chain` + `strong`；reason 点明同一事件/活动/对象
- `同一赛道，各自在做` / `同一公司，不同触点` → `parallel_tracks` + `parallel`；reason 可写「非同一事件、各自推进」
- `同一件事，两个部门各知一半` → `info_complement` + `strong`
- `一方接触，另一方用得上` / 海外类 → 通常 **skip**（单边不成卡）；仅当 evidence 已覆盖 ≥2 实线团队时可 keep 为对应 label + `watch`/`one_sided`
- `skip` 时 relation_type 可为空，`decision_tier` 为 `skip`

## 标签库（不得自创）
两处记录待核对 · 一方有需求，另一方尚未接触 · 一方接触了，另一方正在接触 · 已公开报道，内部也在用 · 同一件事，两个部门各知一半 · 采访对象也是客户 · 两个部门各有判断 · 已联动 · 一方接触，另一方用得上 · 外部在热聊，我们还没碰 · 海外接触，国内可能承接 · 海外新发现，国内尚未接触 · 中英文站同周各自成稿 · 一方报道了，另一方在接触 · 同一赛道，各自在做 · 同一公司，不同触点 · 已排期，内容侧待安排
