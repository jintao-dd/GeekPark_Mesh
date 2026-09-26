# Writer 质量基线（改前钉死）

> 记录时间：2026-09-16  
> 用途：本批（Writer 账本 + grounding 抹后重写 + 近重降档 + 半公式闸）对照起点

## 版本

| 项 | 值 |
|----|-----|
| Git | `b76ab0adcedc`（`fix(writer): drop truncated body synthesize…`） |
| Image tag | `geekpark-mesh:2026-09-16-b76ab0adcedc` |
| tmesh | 同 tag / `MESH_GIT_SHA=b76ab0adcedc` |
| prod | 同 tag / `MESH_GIT_SHA=b76ab0adcedc` |

## 改前 Preview 快照（force 后）

| 环境 | slug | n_relations | empty_body | 合成/另一侧 | 备注 |
|------|------|-------------|------------|-------------|------|
| tmesh | 2026-09-08 | 3 | 1 | 0 | 英伟达近重成对；1 张 ungrounded 空 body |
| prod | 2026-09-08 | 4 | 2 | 0 | 空 body 均 `_body_omitted_ungrounded` |
| prod | 2026-09-15 | 8 | 0 | 0 | 质量最好；阿里云/面壁近重；收尾「可对照」套话 |

## 已知缺口（本批要修）

1. ungrounded 抹 body 后不重写 → 空卡  
2. 近重只 Warning、仍双卡上读者；`suspected_duplicate` 字段未落地  
3. 半公式标题 / body 收尾套话 / 标签词进 body  
4. Writer 链无结构化账本（Decision/Claim 已有 audit）

## 本批验收主场

- **prod `2026-09-15`** 重生对照；tmesh 仅冒烟  
- 不 remine；不并行开多期 Preview
