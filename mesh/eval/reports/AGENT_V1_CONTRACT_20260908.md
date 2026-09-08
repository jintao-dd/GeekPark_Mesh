# Agent v1 契约测试报告（⑦ + 真实 Adapter）

> 日期：2026-09-08  
> 范围：本地/HTTP Agent Harness + **真实** `ask.published` / `ask.relations_summary`  
> **不依赖飞书**；⑧ 仍关闭

## 结论

**契约 + 真实冒烟 20/20 PASS。**

- 编排契约（原 18）保持  
- 新增 2 条真实 Evidence 冒烟：published 检索 / relations 摘要  

当前可证明：**Agent 接真实 Mesh Published 数据后契约成立**。  
下一步闸门：**Mesh + Agent 全量验收** → 通过后才开 ⑧ 飞书 MVP。

## 命令

```bash
cd mesh
python -m pytest tests/test_agent_v1_contract.py -v
```

## Adapter

| Tool | 实现 |
|------|------|
| `ask.published` | `ask_engine.prepare` + EvidenceRef（chunk/item/ctx）；IssueRef 钉期日期，避免「本周」滚窗漏期 |
| `ask.relations_summary` | `published_json.relations` + `relation_display` reader 口径 + `ev:item:` / `ev:rel:` |

可选：`MESH_AGENT_USE_LLM=1` 时 published 走 LLM 成文；默认用可追溯上下文摘要（验收不绑死 LLM）。

## 覆盖

| 场景 | 结果 |
|------|------|
| 原 18 契约矩阵 | PASS |
| 真实 published → Evidence | PASS |
| 真实 relation → Evidence | PASS |

## 下一闸门（不做飞书）

```
⑦ 20/20（含真实 adapter）
        ↓
⭐ Mesh + Agent 全量验收
  （流程 / 逻辑 / 速度 / 容错 / 并发 / Ask25·Gold·Claim·T13·发布边界）
        ↓
通过 → ⑧ 飞书 MVP（只接线）
```
