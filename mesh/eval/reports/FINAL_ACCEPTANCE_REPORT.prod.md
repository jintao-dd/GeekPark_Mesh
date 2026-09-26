# Mesh Ask 最终验收报告

- 生成时间：2026-09-07T17:48:19
- 语料环境：**prod_pg**
- 生产 PG 全库：**是（多期 published）**

## 汇总

| 维度 | 结果 |
|------|------|
| 25 题检索 | 25/25 pass |
| E2E LLM | 未跑 |
| 二轮 follow-up | 未跑 |
| SSE 断线回放 | 未跑 |

## 已验证 / 未验证 / 已知限制

### verified
- 生产 PG 全库多期语料已加载
- 25 题逐条指标（检索 25/25 通过）

### not_verified

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
| e08 | Y | — | independent | structured | 11 | — |
| e09 | Y | — | independent | structured | 19 | — |
| e10 | Y | — | independent | structured | 30 | — |
| e11 | Y | — | independent | hybrid | 35 | — |
| e12 | Y | — | independent | hybrid | 40 | — |
| e13 | Y | — | independent | hybrid | 40 | — |
| e14 | Y | — | independent | hybrid | 40 | — |
| e15 | Y | — | independent | hybrid | 40 | — |
| e16 | Y | — | followup | hybrid | 40 | — |
| e17 | Y | — | followup | hybrid | 40 | — |
| e18 | Y | — | independent | hybrid | 40 | — |
| e19 | Y | — | independent | hybrid | 40 | — |
| e20 | Y | — | independent | guard | 0 | — |
| e25 | Y | — | independent | guard | 0 | — |
| e21 | Y | — | independent | structured | 2 | — |
| e22 | Y | — | independent | hybrid | 0 | — |
| e23 | Y | — | independent | hybrid | 40 | — |
| e24 | Y | — | independent | hybrid | 33 | — |

## 逐题详情

### e01: 商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？
- tags: structured, intersect
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 0, "n_context": 0, "total": 0, "date_from": "2026-06-09", "date_to": null, "latency_ms": 5, "titles": [], "issues": [], "direct_answer": true}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - min_total: ok — total=0

### e02: 编辑部接触了面壁智能吗
- tags: entity, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "编辑部接触了面壁智能吗", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 4058, "titles": ["赵越", "任少卿", "苏昊", "小鹏汽车", "robotuo", "溯光灵迹"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=hybrid
  - context_has_面壁: ok — found=True

### e03: 具身智能有哪些公司
- tags: topic, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "具身智能有哪些公司", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 3938, "titles": ["溯光灵迹", "robotuo", "小鹏汽车", "影眸科技", "擎羽科技", "具身智能与 Physical AI"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=hybrid
  - min_context: ok — n_ctx=40
  - context_has_具身: ok — found=True

### e04: 还有哪些
- tags: followup
- routing: `{"kind": "followup", "reason": "anaphora", "search_q": "具身智能有哪些公司 还有哪些 优必选", "parent_analysis_id": "eval-prior"}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 2899, "titles": ["优必选", "Calvin Zhou", "具身智能", "具身智能", "具身智能", "源策"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=followup reason=anaphora
  - no_answer_in_search_q: ok — search_q=具身智能有哪些公司 还有哪些 优必选
  - min_context: ok — n_ctx=40

### e05: 面壁智能
- tags: short, independent
- routing: `{"kind": "independent", "reason": "standalone", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 2555, "titles": ["面壁智能 · 詹杨帆", "面壁智能", "端侧 AI 与芯片", "面壁智能 · 詹杨帆", "面壁智能", "面壁智能"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=standalone
  - context_has_面壁: ok — found=True

### e06: 面壁智能
- tags: team_scope
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 2276, "titles": ["面壁智能 · 詹杨帆", "面壁智能", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
- rerank/team baseline: `{"old_fts_with_owner_team": 0, "fts_total": 24, "new_hybrid": 24, "date_from": "2026-06-09", "date_to": null}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40
  - context_has_面壁: ok — found=True

### e07: 面壁智能
- tags: slug_scope
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 1875, "titles": ["面壁智能", "面壁智能 · 詹杨帆", "面壁智能", "面壁智能", "面壁智能", "面壁智能"], "issues": ["2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40
  - scope_issues: ok — issues=['2026-8-17'] expect=['2026-8-17']

### e08: 商业化团队跟进了但编辑部还没接触的主体有哪些？
- tags: structured, diff
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "商业化团队跟进了但编辑部还没接触的主体有哪些？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 11, "n_context": 11, "total": 11, "date_from": "2026-06-09", "date_to": null, "latency_ms": 3, "titles": ["PHY", "SAP / 美图 / AWS 等", "优必选 / 大疆 / 影龙 等", "方寸跃迁", "智能云", "追觅"], "issues": ["2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=diff expect=diff

### e09: 海外团队有接触、国内团队还没跟进的公司有哪些？
- tags: structured, overseas
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "海外团队有接触、国内团队还没跟进的公司有哪些？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 19, "n_context": 19, "total": 19, "date_from": "2026-06-09", "date_to": null, "latency_ms": 5, "titles": ["Mentiforce", "AdsGency AI", "Flowtica", "Generation Lab", "HeyGen", "Pure Global"], "issues": ["2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=overseas_gap expect=overseas_gap

### e10: 各团队最近关注了哪些硬件相关话题？
- tags: structured, by_team
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "各团队最近关注了哪些硬件相关话题？", "parent_analysis_id": null}`
- recall: `{"mode": "structured", "n_hits": 69, "n_context": 30, "total": 69, "date_from": "2026-06-09", "date_to": null, "latency_ms": 2, "titles": ["具身智能与 Physical AI", "深圳 Physical AI 近场研究", "内容矩阵与账号", "李源 · 具身智能账号矩阵", "视频号与内容传播", "AI 硬件产业带"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=by_team expect=by_team

### e11: 詹杨帆最近有什么动态
- tags: entity, person
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "詹杨帆最近有什么动态", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 35, "n_context": 35, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 4245, "titles": ["詹杨帆", "詹杨帆", "詹杨帆", "詹杨帆", "詹杨帆", "詹杨帆"], "issues": ["2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=35
  - context_has_詹杨: ok — found=True

### e12: 吉利银河 智能座舱
- tags: entity, topic
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "吉利银河 智能座舱", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 3184, "titles": ["面壁智能", "面壁智能 · 詹杨帆", "吉利银河", "吉利银河", "智能座舱", "智能座舱"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e13: 近30天编辑部接触了谁
- tags: time_window, team
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "近30天编辑部接触了谁", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-08-08", "date_to": null, "latency_ms": 2819, "titles": ["戴若犁", "Agent 与工程化能力", "AI 硬件产业带", "Physical AI 与练兵场", "赵越", "任少卿"], "issues": ["2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e14: 全部历史以来具身智能有哪些公司
- tags: time_all
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "全部历史以来具身智能有哪些公司", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": null, "date_to": null, "latency_ms": 3159, "titles": ["具身智能与 Physical AI", "擎羽科技", "几硕", "影眸科技", "内容矩阵与账号", "面壁智能 · 詹杨帆"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e15: 编辑部和商业化团队两边同时跟进了哪些客户？
- tags: cross_cue, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "编辑部和商业化团队两边同时跟进了哪些客户？", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 9458, "titles": ["Stripe", "阿里千问", "飞书未来无限大会", "陈宇森", "原力农机", "5G项目"], "issues": ["2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e16: 那家最近怎么样
- tags: followup, anaphora
- routing: `{"kind": "followup", "reason": "anaphora", "search_q": "编辑部接触了面壁智能吗 那家最近怎么样", "parent_analysis_id": "eval-prior"}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 4097, "titles": ["联想", "Cecilia Cai", "张钧泓", "Andrew Chen", "杨硕", "具身智能"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=followup reason=anaphora
  - no_answer_in_search_q: ok — search_q=编辑部接触了面壁智能吗 那家最近怎么样
  - min_context: ok — n_ctx=40

### e17: 他们对比一下
- tags: followup, weak
- routing: `{"kind": "followup", "reason": "anaphora", "search_q": "编辑部和商业化团队两边同时跟进了哪些客户？ 他们对比一下", "parent_analysis_id": "eval-prior"}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 3598, "titles": ["拓竹", "原力农机", "杨硕", "Founder Park", "豆包", "具身智能"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=followup reason=anaphora
  - min_context: ok — n_ctx=40

### e18: 硅谷 BD 团队有没有接触面壁智能
- tags: team_in_q, entity
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "硅谷 BD 团队有没有接触面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 2830, "titles": ["矽递科技", "短测未来", "Calvin Zhou", "Aaron Li", "Ali Agha", "Allen Ren"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e19: 面壁智能
- tags: date_scope
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "面壁智能", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-08-01", "date_to": "2026-08-31", "latency_ms": 3151, "titles": ["面壁智能 · 詹杨帆", "面壁智能", "端侧 AI 与芯片", "面壁智能 · 詹杨帆", "面壁智能", "面壁智能"], "issues": ["2026-08-21", "2026-8-17"], "direct_answer": false}`
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
- recall: `{"mode": "structured", "n_hits": 2, "n_context": 2, "total": 2, "date_from": "2026-06-09", "date_to": null, "latency_ms": 1, "titles": ["英伟达：商业化做赞助活动，编辑部做播客", "字节豆包 · Agent 巨头战役"], "issues": ["2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - mode: ok — mode=structured
  - set_op: ok — set_op=intersect expect=intersect

### e22: Global Partnership 团队本周接触了谁
- tags: team, alias
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "Global Partnership 团队本周接触了谁", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 0, "n_context": 0, "total": null, "date_from": "2026-09-07", "date_to": "2026-09-07", "latency_ms": 2889, "titles": [], "issues": [], "direct_answer": true}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=0

### e23: 视频号团队最近关注了什么
- tags: team, topic
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "视频号团队最近关注了什么", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 40, "n_context": 40, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 2555, "titles": ["内容矩阵与账号", "AI 硬件测评与形态", "Agent 与模型商品化", "宇树 · 王兴兴", "字节豆包", "具身智能与 Physical AI"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=40

### e24: 哪些是一队接触过、另一队该知道的？
- tags: relation_cue, hybrid
- routing: `{"kind": "independent", "reason": "no_history", "search_q": "哪些是一队接触过、另一队该知道的？", "parent_analysis_id": null}`
- recall: `{"mode": "hybrid", "n_hits": 33, "n_context": 33, "total": null, "date_from": "2026-06-09", "date_to": null, "latency_ms": 4047, "titles": ["阿里云千问", "追觅", "内容矩阵与账号", "OPPO", "cumora", "意图即服务"], "issues": ["2026-08-21", "2026-09-01", "2026-8-17"], "direct_answer": false}`
  - routing: ok — kind=independent reason=no_history
  - min_context: ok — n_ctx=33
