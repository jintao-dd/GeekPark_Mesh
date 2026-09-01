# Mesh Ask 最终验收报告

- 生成时间：2026-08-31T04:32:48
- 语料环境：**golden_sqlite(_issue_2026-8-17.json)**
- 生产 PG 全库：**否（本地黄金 SQLite）**

## 汇总

| 维度 | 结果 |
|------|------|
| 25 题检索 | 25/25 pass |
| E2E LLM | 未跑 |
| 二轮 follow-up | 未跑 |
| SSE 断线回放 | 未跑 |

## 已验证 / 未验证 / 已知限制

### verified
- 25 题逐条指标（检索 25/25 通过）

### not_verified
- 生产 PG 全库（需在 prod 容器重跑）

### known_limits
- 黄金语料以 2026-8-17 为主，不等同生产 PG 全库
- LLM E2E 存在输出波动

### failures

## 25 题逐题结果

| ID | PASS | layer | routing | mode | n_ctx | 关键检查 |
|----|------|-------|---------|------|-------|----------|
| e01 | Y | — | independent | structured | 0 | — |
| e02 | Y | — | independent | hybrid | 40 | — |
| e03 | Y | — | independent | hybrid | 40 | — |
| e04 | Y | — | followup | hybrid | 40 | — |
| e05 | Y | — | independent | hybrid | 40 | — |
| e06 | Y | — | independent | hybrid | 40 | — |
| e07 | Y | — | independent | hybrid | 40 | — |
| e08 | Y | — | independent | structured | 25 | — |
| e09 | Y | — | independent | structured | 28 | — |
| e10 | Y | — | independent | structured | 30 | — |
| e11 | Y | — | independent | hybrid | 40 | — |
| e12 | Y | — | independent | hybrid | 36 | — |
| e13 | Y | — | independent | hybrid | 40 | — |
| e14 | Y | — | independent | hybrid | 40 | — |
| e15 | Y | — | independent | hybrid | 40 | — |
| e16 | Y | — | followup | hybrid | 40 | — |
| e17 | Y | — | followup | hybrid | 40 | — |
| e18 | Y | — | independent | hybrid | 40 | — |
| e19 | Y | — | independent | hybrid | 40 | — |
| e20 | Y | — | independent | guard | 0 | — |
| e25 | Y | — | independent | guard | 0 | — |
| e21 | Y | — | independent | structured | 8 | — |
| e22 | Y | — | independent | hybrid | 0 | — |
| e23 | Y | — | independent | hybrid | 40 | — |
| e24 | Y | — | independent | hybrid | 19 | — |

## 逐题详情

### e01: 商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？
- tags: structured, intersect
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 0, "n_context": 0, "total": 0, "date_from": "2026-06-02", "date_to": null, "latency_ms": 4, "titles": [], "issues": [], "direct_answer": true}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - min_total: ok — total=0

### e02: 编辑部接触了面壁智能吗
- tags: entity, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "编辑部接触了面壁智能吗", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1462, "titles": ["面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=hybrid
  - context_has_面壁: ok — found=True

### e03: 具身智能有哪些公司
- tags: topic, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "具身智能有哪些公司", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 2814, "titles": ["具身智能", "具身智能", "几硕", "短测未来", "李源", "具身智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=hybrid
  - min_context: ok — n_ctx=40
  - context_has_具身: ok — found=True

### e04: 还有哪些
- tags: followup
- routing: `{"kind": "followup", "reason": "anaphora", "search_q": "具身智能有哪些公司 还有哪些 优必选", "parent_analysis_id": "eval-prior"}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1773, "titles": ["具身智能", "具身智能", "具身智能", "源策", "短测未来", "李源"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=followup reason=anaphora
  - no_answer_in_search_q: ok — search_q=具身智能有哪些公司 还有哪些 优必选
  - min_context: ok — n_ctx=40

### e05: 面壁智能
- tags: short, independent
- routing: `{"kind": "independent", "reason": "standalone", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1727, "titles": ["面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=standalone
  - context_has_面壁: ok — found=True

### e06: 面壁智能
- tags: team_scope
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 619, "titles": ["面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
- rerank/team baseline: `{"old_fts_with_owner_team": 0, "fts_total": 24, "new_hybrid": 24, "date_from": "2026-06-02", "date_to": null}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40
  - context_has_面壁: ok — found=True

### e07: 面壁智能
- tags: slug_scope
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1395, "titles": ["面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40
  - scope_issues: ok — issues=['2026-8-17'] expect=['2026-8-17']

### e08: 商业化团队跟进了但编辑部还没接触的主体有哪些？
- tags: structured, diff
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "商业化团队跟进了但编辑部还没接触的主体有哪些？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 25, "n_context": 25, "total": 25, "date_from": "2026-06-02", "date_to": null, "latency_ms": 2, "titles": ["OPPO", "亚马逊广告", "天机智能、跃迁", "追觅", "阿里云千问", "AI 小镇"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=diff expect=diff

### e09: 海外团队有接触、国内团队还没跟进的公司有哪些？
- tags: structured, overseas
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "海外团队有接触、国内团队还没跟进的公司有哪些？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 28, "n_context": 28, "total": 28, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1, "titles": ["AdsGency AI", "Eigent AI", "Flowtica", "Generation Lab", "HeyGen", "Mentiforce"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=overseas_gap expect=overseas_gap

### e10: 各团队最近关注了哪些硬件相关话题？
- tags: structured, by_team
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "各团队最近关注了哪些硬件相关话题？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 55, "n_context": 30, "total": 55, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1, "titles": ["具身智能与 Physical AI", "深圳 Physical AI 近场研究", "破壳创智", "内容矩阵与账号", "李源 · 具身智能账号矩阵", "视频号与内容传播"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=by_team expect=by_team

### e11: 詹杨帆最近有什么动态
- tags: entity, person
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "詹杨帆最近有什么动态", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1530, "titles": ["网信办", "AI智能体安全", "以色列", "阿里", "中兴通讯", "面壁智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40
  - context_has_詹杨: ok — found=True

### e12: 吉利银河 智能座舱
- tags: entity, topic
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "吉利银河 智能座舱", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 36, "n_context": 36, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1603, "titles": ["面壁智能", "面壁智能", "面壁智能", "面壁智能 · 詹杨帆", "具身智能 · 银河通用与四家团队", "吉利银河"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=36

### e13: 近30天编辑部接触了谁
- tags: time_window, team
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "近30天编辑部接触了谁", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-08-01", "date_to": null, "latency_ms": 1791, "titles": ["阿里", "零跑汽车", "威诚资本", "中兴通讯", "熠序科技", "AI智能体安全"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e14: 全部历史以来具身智能有哪些公司
- tags: time_all
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "全部历史以来具身智能有哪些公司", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": null, "date_to": null, "latency_ms": 1733, "titles": ["具身智能", "具身智能", "几硕", "短测未来", "李源", "具身智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e15: 编辑部和商业化团队两边同时跟进了哪些客户？
- tags: cross_cue, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "编辑部和商业化团队两边同时跟进了哪些客户？", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1865, "titles": ["vivo", "阿里千问", "vivo", "杨硕", "Founder Park", "豆包"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e16: 那家最近怎么样
- tags: followup, anaphora
- routing: `{"kind": "followup", "reason": "anaphora", "search_q": "编辑部接触了面壁智能吗 那家最近怎么样", "parent_analysis_id": "eval-prior"}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1793, "titles": ["杨硕", "具身智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=followup reason=anaphora
  - no_answer_in_search_q: ok — search_q=编辑部接触了面壁智能吗 那家最近怎么样
  - min_context: ok — n_ctx=40

### e17: 他们对比一下
- tags: followup, weak
- routing: `{"kind": "followup", "reason": "anaphora", "search_q": "编辑部和商业化团队两边同时跟进了哪些客户？ 他们对比一下", "parent_analysis_id": "eval-prior"}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1579, "titles": ["杨硕", "Founder Park", "豆包", "具身智能", "PPIO", "擎羽科技"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=followup reason=anaphora
  - min_context: ok — n_ctx=40

### e18: 硅谷 BD 团队有没有接触面壁智能
- tags: team_in_q, entity
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "硅谷 BD 团队有没有接触面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1543, "titles": ["面壁智能 · 詹杨帆", "面壁智能", "面壁智能", "面壁智能", "面壁智能 · 詹杨帆", "端侧 AI 与芯片"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e19: 面壁智能
- tags: date_scope
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-08-01", "date_to": "2026-08-31", "latency_ms": 1790, "titles": ["面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e20: xyzrandomquery999nodata
- tags: no_evidence, guard
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "xyzrandomquery999nodata", "parent_analysis_id": null}`
- recall: `{"mode": "guard", "n_hits": 0, "n_context": 0, "total": null, "date_from": null, "date_to": null, "latency_ms": 0, "titles": [], "issues": [], "direct_answer": true}`
  - routing: ok — kind=independent reason=no_history
  - direct_answer: ok — direct=True
  - max_hits: ok — n_hits=0 max=0

### e25: asdfghjklqwertyuiop123456
- tags: no_evidence, guard
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "asdfghjklqwertyuiop123456", "parent_analysis_id": null}`
- recall: `{"mode": "guard", "n_hits": 0, "n_context": 0, "total": null, "date_from": null, "date_to": null, "latency_ms": 0, "titles": [], "issues": [], "direct_answer": true}`
  - routing: ok — kind=independent reason=no_history
  - direct_answer: ok — direct=True
  - max_hits: ok — n_hits=0 max=0

### e21: 编辑部和商业化团队在可同步关系上有哪些重叠？
- tags: structured, relation
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "编辑部和商业化团队在可同步关系上有哪些重叠？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 8, "n_context": 8, "total": 8, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1, "titles": ["字节豆包 · Agent 巨头战役", "阿里千问", "AI 助听器 · 飞声", "WorkBuddy", "vivo", "具身智能 · 银河通用与四家团队"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=intersect expect=intersect

### e22: Global Partnership 团队本周接触了谁
- tags: team, alias
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "Global Partnership 团队本周接触了谁", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 0, "n_context": 0, "total": null, "date_from": "2026-08-31", "date_to": "2026-08-31", "latency_ms": 521, "titles": [], "issues": [], "direct_answer": true}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=0

### e23: 视频号团队最近关注了什么
- tags: team, topic
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "视频号团队最近关注了什么", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1526, "titles": ["MiniMax", "具身智能", "宇树 · 王兴兴", "AI 硬件测评与形态", "字节豆包", "内容矩阵与账号"], "issues": ["2026-08-14", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e24: 哪些是一队接触过、另一队该知道的？
- tags: relation_cue, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "哪些是一队接触过、另一队该知道的？", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 19, "n_context": 19, "total": null, "date_from": "2026-06-02", "date_to": null, "latency_ms": 1613, "titles": ["海量科普", "MiniMax", "liblib", "尚诚起源", "TUTTI", "未来不远"], "issues": ["2026-08-14"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=19
