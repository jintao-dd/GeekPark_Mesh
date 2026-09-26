# 组织 ↔ 业务队 交叉修复与论证

- 日期：2026-09-22
- commit：见 git log（本文件随代码合入）

## 修复前的别扭（实测）

| 探针 | 结果 |
|---|---|
| `db.normalize_team("海外拓展")` | `None`（不在 ingest.TEAMS） |
| `identity.normalize_team("海外拓展")` | `"海外拓展"`（私有 `_EXTRA_COLLEAGUE_TEAMS`） |
| 赵思琪 `primary_team` | 海外拓展 |
| 周报 `owner_team='海外拓展'` | **永远查不到**（值从未写入） |
| roster 覆盖 ingest.TEAMS | 7 个队无人挂载（硅谷 BD / 英文站 / 播客 / 视频号 / …） |
| users.display | `杜锦涛54564545` 脏后缀 |

同一个人：身份系统一个队名，周报系统从来不用这个队名 → 「数据没交叉」。

## 修复后的模型

```
飞书部门名 ──dept_team_map──▶ canonical_team ∈ ingest.TEAMS
                                      │
roster.teams[] ──┐                    │
users.team ──────┼──▶ db.normalize_team ◀── 唯一归一
                 │         │
identity.normalize_team ───┘  （仅再过滤占位桶）
```

1. **「海外拓展」正式进入 `ingest.TEAMS`**（可上传、可过滤、可身份主队）
2. **删除 identity 私有队名单**；`identity.normalize_team` 委托 `db.normalize_team`
3. **parent_team 保留**：问「品牌创意」仍纳入海外拓展人员（org_directory）
4. **周报-only 队**（硅谷 BD 等）允许 roster 无人：一致性检查标为 warning，不 fail
5. **显示名消毒**：`杜锦涛54564545` → `杜锦涛`（登录 upsert + 存量修复）

## 论证（自动化）

```bash
PYTHONPATH=. python eval/check_org_team_consistency.py
# 期望：PASS；split_brain=0；haiwai_unified=True；person_no_biz_team=0
```

单测：`tests/test_org_team_consistency.py` + 更新后的 `test_dept_team_map.py`。

不变量：

1. ∀ 字符串 t：`db.normalize_team(t)` 与 `identity.normalize_team(t)` 在「业务主队」语义上相等（占位桶两侧都不当主队）
2. `海外拓展 ∈ ingest.TEAMS` 且两边归一均为 `"海外拓展"`
3. 叶子部门 `创新技术/创意视频/品牌设计` → `品牌创意团队`（不变）
4. roster 70 人每人至少落一个业务队

## 「我们团队」口径（全局，2026-09-22）

**一律 = Mesh `primary_team`（业务队）**，全公司同一规则：

- 品牌创意下创新技术 / 创意视频 / 品牌设计 → 问「我们团队」= 整队品牌创意，**不再按叶子切**
- 编辑部 / 商业化 / 海外拓展 / … 同理：只认 `identity.primary_team`，不走飞书叶子
- 显式问「创新技术有谁」仍走 `asker_teammates`（通讯录树）

实现：`org_directory.our_team_members`；调用方 loop / workers / company_context / mouth / ontology / adapters / plan。

## 仍须理解的边界（不是 bug）

- **飞书部门 ≠ 周报桶**：创新技术的人，身份主队是品牌创意（部门映射）；问「创新技术有谁」走通讯录树，问「品牌创意周报」走 owner_team
- **硅谷 BD / 视频号等**：可能没有飞书部门镜像；人脉材料挂周报桶，同事身份挂海外拓展/编辑部等 —— 靠口术与 crm.search 交叉，不靠硬改花名册假挂
