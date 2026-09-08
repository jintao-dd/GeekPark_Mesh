# Agent v1 契约测试报告（⑦ Harness）

> 日期：2026-09-08  
> 范围：本地/HTTP Agent Harness（**不依赖飞书**）  
> 架构：`docs/AGENT_ARCHITECTURE_V1.md` ①～⑦ 冻结

## 结论

**契约测试 18/18 PASS。**  
⑦ 实现可进入「修边 / 接真实 ask_engine 适配器」收口；**尚未**开 ⑧ 飞书接线。

## 命令

```bash
cd mesh
python -m pytest tests/test_agent_v1_contract.py -v
```

## 覆盖矩阵

| 场景 | 结果 |
|------|------|
| help（无数据 Tool） | PASS |
| list_issues（仅 published） | PASS |
| ask.published（恰好 1 次） | PASS |
| ask.relations_summary（恰好 1 次） | PASS |
| 未绑定 unlinked | PASS（无数据 Tool） |
| bound_team_conflict | PASS（无数据 Tool） |
| draft/raw 越权 | PASS（拒绝且无泄漏） |
| bound_team_missing → unfocused | PASS |
| DM vs 群 scope_key | PASS |
| chat team ≠ primary_team | PASS（不改 Person） |
| explicit team + explicit issue | PASS（locked） |
| latest_published fallback | PASS |
| explicit draft issue | PASS（IssueRef=none） |
| 空结果不二次数据 Tool | PASS |
| Tool 自防御 ACL/conflict | PASS |
| Tool 自防御 draft / issue bypass / team bypass | PASS |
| 未注册 Tool | PASS |
| fingerprint ≠ trace | PASS |

## 实现要点

- 包：`mesh/app/agent/`（identity / permission / context / intent / tools / runtime / harness）
- HTTP：`POST /api/agent/v1/message`（登录 viewer+；与飞书无关）
- `ask.published` / `ask.relations_summary` 当前为 **Harness stub**（可注入适配器接 `ask_engine`）；契约层已锁死 ≤1 Tool、Published-only、自防御

## ⑧ 门禁

在下列完成前 **不开飞书 MVP**：

1. （可选）把 stub 换成真实 published 检索适配器并加 1～2 条冒烟  
2. 本报告场景持续绿  
3. 再按「只接线不扩大脑」接飞书消息 → open_id → `handle_message` → 回复
