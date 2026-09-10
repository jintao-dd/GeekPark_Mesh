# Colleague Agent v3 · 一个能懂我们的同事

> **日期：** 2026-09-10  
> **唯一目标：** 飞书里是一个懂 GeekPark、认识你、能查已上线周报的同事——不是装配机器人。  
> **总计划：** Wave 1（本页）→ Wave 2 会话关系 → Wave 3 Memory  

## 失败声明

v2 = Hard + Controller + Schema + Core + Chat + Ask 成文 = **组件化机器人**。已推翻为产品中轴。

## 终局三物

| 物 | 职责 |
|----|------|
| SafetyGate | ACL / 草稿 / 能力边界（规则） |
| Colleague | 唯一主体：听 + 判断 + 说话 |
| Grounded Ask | 周报事实工具（不是第二张嘴） |

Identity / Permission / Published 底座沿用 Architecture v1，**不重做组织工程**；Colleague 必须用满「谁 / 哪队」。

## Wave 1（当前）

```
飞书消息 → SafetyGate → Colleague（1× LLM）
                ├─ speak → 直接回
                ├─ ask_* → Existing Ask → 同声线合成（≤1）
                └─ refuse → 短拒
```

- **旁路** Controller（不再先 Decide Agent）  
- **禁止** Multi-Agent / ReAct / 解冻质量轨  
- 闲聊 ≤1 LLM；带事实 ≤1 Colleague + 1 Ask + ≤1 合成  

验收：真人觉得「像普通同事」；企业事实仍 Evidence + Published-only。

## 废弃

`colleague_controller` 自然语言中轴 · Stage 2A `response_mode` 预算表中轴 · 独立 Decide Agent  

代码可暂留，主路径不再走。

## 开关

`MESH_COLLEAGUE_V3=1`（默认开）· 设 `0` 回退 v2 装配路径（紧急）
