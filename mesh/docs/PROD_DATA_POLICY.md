# Prod 数据与抽检规范

> 2026-08-31 · KG v0 误 publish 回滚后锁定。

## 2026-8-17 回滚记录

| 项 | 回滚前（抽检 publish） | 回滚后（v1） |
|----|------------------------|--------------|
| published_at | 2026-08-31 11:05 | **2026-08-27 09:54** |
| relations | 41 | **14** |
| weak | 0 | **4** |
| with evidence | 41 | **0** |

脚本：`python deploy/rollback_issue_version.py --slug 2026-8-17 --version v1`

## 禁止事项

- **禁止**在生产库对已上线 slug 执行 `prod_kg_spotcheck.py --regen|--publish|--confirm-weak`
- 生产 KG/Ask 验收 **仅只读**：`--audit-only` 或 `eval/run_final_eval.py --corpus prod`（不写 issues）
- 除非设置 `MESH_ALLOW_PROD_PUBLISH=1` 且经 owner 明确批准（默认关闭）

## 下一期真实周报流程

```
sources → pipeline → preview → merge(evidence) → relation_verify → owner 审核 weak → publish
```

不得用脚本绕过 owner 闸门。
