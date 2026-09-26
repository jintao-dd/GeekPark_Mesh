# Colleague Agent v3 · 一个能懂我们的同事

> **日期：** 2026-09-10  
> **状态：** Wave 1 **已落地底座**；产品中轴自 2026-09-11 起迁至 [`COLLEAGUE_AGENT_V4.md`](./COLLEAGUE_AGENT_V4.md)（Agentic Enterprise：Orchestrator / Bounded ReAct / Specialists）。  
> **唯一目标：** 飞书里是一个懂 GeekPark、认识你、能查已上线周报的同事——不是装配机器人。  
> **原 Wave 2/3 线性计划：** 废止；能力并入 v4 五层。  

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
- **当时禁止**开放 Multi-Agent / ReAct（**v4 起改为有笼子的 Specialist + Bounded ReAct**）  
- 闲聊 ≤1 LLM；带事实 ≤1 Colleague + 1 Ask + ≤1 合成（v4 Complex 档放宽为有预算多工具）  
- **质量轨**仍禁止解冻 Ranking / Claim  

验收：真人觉得「像普通同事」；企业事实仍 Evidence + Published-only。

## 废弃

`colleague_controller` 自然语言中轴 · Stage 2A `response_mode` 预算表中轴 · 独立 Decide Agent  

代码可暂留，主路径不再走。

## 开关

`MESH_COLLEAGUE_V3=1`（默认开）· 设 `0` 回退 v2 装配路径（紧急）
