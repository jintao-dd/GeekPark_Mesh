# Feishu Hands · CLI Adapter 生产就绪契约

> **日期：** 2026-09-11  
> **目标：** 把现有 CLI Adapter 从纸面实现验成生产可用运输层；**不重设计 Hands**。  
> **默认：** 在本清单全绿前，生产/默认 backend 保持 **`native`**；CLI 仅金丝雀。

---

## 0. 架构前提（已钉死）

```
Colleague / Tool Contract / 确认闸 / 分桶 / 短期记忆
        ↓ 不变
   Hands Runtime（ops + Envelope）
        ↓ backend flag
   Adapter: native | cli | mcp | mock
```

- 切 CLI = 换 transport，不是换 Runtime。  
- **凭证模型：env provider，禁止交互 login / 本机 keychain 当热路径。**

| 变量 | 用途 |
|------|------|
| `LARKSUITE_CLI_APP_ID` | 应用 ID（可由 `FEISHU_APP_ID` 映射） |
| `LARKSUITE_CLI_APP_SECRET` | 应用 Secret（可由 `FEISHU_APP_SECRET` 映射） |
| `LARKSUITE_CLI_BRAND` | 默认 `feishu` |
| `LARKSUITE_CLI_USER_ACCESS_TOKEN` | 可选；user 能力后置 |
| `MESH_FEISHU_HANDS_BACKEND=cli` | 金丝雀时显式打开 |
| `MESH_FEISHU_CLI_BIN` | 可选；默认 `lark-cli` |

多 worker / recreate：依赖 **同一套 compose env**，不依赖容器可写层里的 login session。

---

## 1. 就绪门禁（全部 ✅ 才允许议「默认改 cli」）

| # | 项 | 判定 |
|---|----|------|
| R1 | 镜像内 `lark-cli --version` 成功 | ☐ |
| R2 | 仅 env 凭证（无 `auth login`）bot 身份可调用 | ☐ |
| R3 | `BACKEND=cli` 不再出现 `cli_not_installed` | ☐ |
| R4 | 读：`feishu.search`（至少 doc）/ `doc.get` Envelope 对齐 | ☐ |
| R5 | 写：prepare→confirm→`doc.create` 成功返回 url/token | ☐ |
| R6 | 创建后公司内获链可读（`tenant_readable` 或等价） | ☐ |
| R7 | 失败可映射到 `scope_denied` / `feishu_api_*` 等（供 `last_block`） | ☐ |
| R8 | `compose recreate` 后 R2–R6 仍过（无本地 login 态） | ☐ |
| R9 | workers=1 稳定；若试 workers>1，仅验证 env 凭证仍可用 | ☐ |
| R10 | native 可一键切回作 fallback | ☐（flag 已具备） |

**未全绿：默认保持 `MESH_FEISHU_HANDS_BACKEND=native`。**

---

## 2. 对等缺口（相对 native）

| 缺口 | 状态 |
|------|------|
| 镜像未装 `lark-cli` | → Phase 1 |
| 未注入 `LARKSUITE_CLI_*` | → Phase 1 |
| `doc.create` 后无 `tenant_readable` | → Phase 2（create 后调 share helper） |
| 命令未统一 `--as bot` | → Phase 2 |
| 部分读工具 Envelope 空/未 normalize | → Phase 2 |
| CLI 错误语义模糊 | → Phase 2 |

---

## 3. 金丝雀步骤（tmesh）

1. 发版含 cli 的镜像；默认仍 `native`  
2. 临时：`MESH_FEISHU_HANDS_BACKEND=cli`（tmesh only）  
3. `docker exec … lark-cli --version`  
4. 容器内：`lark-cli im +messages-send --as bot --dry-run …` 或只读 shortcut  
5. 飞书私聊：创建文档→确认；查日志 backend=cli；查链接公司内可读  
6. recreate 后再跑一遍  
7. 对照表：同输入 native vs cli  

Prod **不**默认开 cli，直到 R1–R10 全绿 + tmesh 观察通过。

---

## 4. 明确不做

- 不重设计 Hands / 不加 Planner  
- 不把 26 Skills 暴露给 Brain  
- 不在 entrypoint 跑交互 `auth login`  
- 不为 CLI 先默认开 multi-worker  
- 不与 MCP 默认缠在一起（MCP 另里程碑）

---

## 5. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-09-11 | 首版契约；与 Phase 1/2 落地同步启动 |
