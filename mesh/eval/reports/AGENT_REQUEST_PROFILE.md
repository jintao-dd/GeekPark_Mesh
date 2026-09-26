# Agent Request Profile · Performance Sprint

**结论先说：** Capacity 里 100s+ / `llm_used=0` **不是**「多个 semantic judge 串行」。  
主因是 **每次请求仍在打 Embedding API**（`qwen3-embedding-8b`，timeout=120s）；当时 tmesh 的 `llm.py`/`providers` 过旧，`task=` / `get_provider(model=)` 直接 TypeError，answer/semantic **根本没跑成**。

## 单请求拆速（修复代码后 · tmesh · 2026-09-09）

### Q1 · Capacity 同款（软问）`编辑部关注了哪些话题或公司`

| 阶段 | ms | 说明 |
|------|-----|------|
| **embed** | **6172** | 1× `embed_texts`（串行） |
| retrieve+rank+assemble（prepare−embed） | ~471 | 便宜 |
| semantic gate | 0 | **enter=false** → 不进 judge |
| semantic LLM | **0** | call_count=0 |
| **answer LLM** | **13101** | 1× `task=answer` |
| **tool total** | **19751** | |

- `semantic_call_count=0`
- `answer_call_count=1`，latency ≈ 13.1s
- LLM 调用串行（本请求只有 1 次）
- dominant = **answer_llm**（健康路径下）

### Q2 · 强 claim（应进 semantic）`资料能否证明它已经量产？`

| 阶段 | ms | 说明 |
|------|-----|------|
| embed | 1758 | 1× |
| retrieve+… | ~471 | |
| semantic gate | enter=**true** | reasons: det_strong_claim, proof_frame |
| **semantic LLM** | **3555** | **1×** `task=semantic` |
| answer LLM | 0 | abstain（insufficient） |
| **tool total** | **5784** | |

- `semantic_call_count=1`（**不是 N 次**）
- 与 answer **串行**（本请求无 answer）
- dominant = claim_support / semantic_llm

## 对 Capacity「138s + llm_used=0」的归因

1. **Embedding 默认开启**  
   - `MESH_EMBED_MODEL=qwen3-embedding-8b`  
   - `MESH_EMBED_BASE_URL=https://kspmas.ksyun.com/v1`  
   - `embeddings.enabled()` 默认 `"1"`；与质量线「Vector OFF」**不一致**——prepare 仍 `embed_one`。  
   - HTTP timeout=**120s**；并发时 embed 变慢可单独吃满墙钟。

2. **当时 LLM 路径是坏的**（已修同步）  
   - 容器缺新 `llm.call(..., task=)` / `get_provider(model=)`  
   - answer/semantic 秒级 TypeError → `llm_used=0`，回落摘要  
   - 墙钟 ≈ **embed（±排队）**，不是 semantic×N。

3. **代码路径上 semantic 最多 1 次/请求**  
   - `apply_semantic_extension` → 至多一次 `llm_semantic_judge`  
   - 无「judge#1 + judge#2 + judge#3」循环。

## 修复后健康预算（单请求 · 参考）

```
soft fact:   embed ~6s  + answer ~13s  ≈ 20s
strong claim: embed ~2s + semantic ~3.5s ≈ 6s（abstain）
```

Capacity 100s+ 属于 **embed 超时/拥堵 × LLM 未真正执行** 的失真态，不能用来定 semantic 并行策略。

## 建议下一刀（仍停 Feishu）

1. **对齐冻结：生产/压测默认 `MESH_EMBED_ENABLED=0`（或 VECTOR OFF 真正关掉 embed_one）**，再测一刀 C=1 P95。  
2. 确认 tmesh/prod `llm.py` + `providers` 已同步（本次已补）。  
3. 答案默认 Sonnet，勿用 Opus 测吞吐。  
4. 再谈队列/并发上限。

## 产物

- `eval/run_agent_request_profile.py`
- `eval/reports/AGENT_REQUEST_PROFILE.tmesh.json`
- `eval/reports/AGENT_REQUEST_PROFILE.tmesh.md`
