# Feishu Hands · 开放平台权限开通清单

应用：`cli_aa0fcf683e385beb`（tmesh / prod 共用）

> 飞书**不能**用服务端密钥自动开通权限；必须在开放平台勾选 → 发布版本 →（部分敏感权限）企业管理员审批。  
> 下面链接可一键跳到「申请该 scope」页；建议一次性开齐后点「创建版本并发布」。

## 控制台入口

- 应用总览：https://open.feishu.cn/app/cli_aa0fcf683e385beb  
- 权限管理：https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth  
- 版本管理 / 发布：https://open.feishu.cn/app/cli_aa0fcf683e385beb/version  

## Hands 10 能力 → 建议开通（应用身份 tenant / bot）

| 能力 | 建议 scope（开任一即可满足该 API 的，按官方文档） | 一键申请 |
|------|--------------------------------------------------|----------|
| 思考卡片 Cardkit | `cardkit:card:write` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=cardkit:card:write) |
| 发消息 IM | `im:message` 或 `im:message:send_as_bot` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=im:message:send_as_bot) |
| 读群消息 / 讨论摘要 | `im:message` / `im:message.group_msg` / `im:chat:readonly` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=im:message) |
| 列群 | `im:chat:readonly` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=im:chat:readonly) |
| 创建文档 | `docx:document` 或 `docx:document:create` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=docx:document) |
| 读文档 / 写正文块 | `docx:document` / `docx:document:readonly` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=docx:document) |
| 云文档搜索（更稳） | `drive:drive` / `drive:drive:readonly` / `search:docs:read` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=drive:drive:readonly) |
| Wiki 搜索 | `wiki:wiki` / `wiki:wiki:readonly` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=wiki:wiki:readonly) |
| 日程列表 | `calendar:calendar:readonly` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=calendar:calendar:readonly) |
| 创建日程 | `calendar:calendar` | [开](https://open.feishu.cn/app/cli_aa0fcf683e385beb/auth?q=calendar:calendar) |

## 复制用 scope 清单（控制台搜索粘贴）

```
cardkit:card:write
im:message
im:message:send_as_bot
im:chat:readonly
im:message.group_msg
docx:document
docx:document:create
docx:document:readonly
docs:permission.setting:write_only
drive:drive
drive:drive:readonly
wiki:wiki:readonly
calendar:calendar
calendar:calendar:readonly
```

## 开通后必做

1. **权限管理**里勾选上述权限并保存  
2. **创建版本 → 申请发布 → 全量发布**（未发布=线上仍无权限）  
3. 若提示「需管理员审批」，到飞书管理后台 → 工作台 → 应用审核 通过  
4. 机器人能力保持开启；测试群把机器人拉进去  

## 创建文档默认分享

Agent 创建 docx 后会调用 Drive v2 `link_share_entity=tenant_readable`（公司内获链可读）。  
需要 `docs:permission.setting:write_only` 或 `drive:drive` / `docx:document` 之一。

## 用户身份（user_access_token）说明

云文档「全库搜索」官方接口通常要 **user_access_token**。  
仅开应用身份权限 **不够** 做「搜全员可见的云文档」；那条需要用户 OAuth（后续单独接）。  
当前 Hands 在缺 user token 时会诚实失败，不会编造结果。

## 自检

开通并发布后，在群里再试：

```
创建一个新文档详细的介绍一下你自己
```

确认写入；若仍失败，把机器人回的错误码 / `feishu_api_XXXX` 发我，对照上表补缺。
