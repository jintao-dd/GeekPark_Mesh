# Runtime Execution · Vector OFF 热路径

> Quality Graph 定义「应该怎么判」；Runtime 决定「这次实际跑什么」。  
> 本文件只记执行纪律，不改 Claim / Ranking / Gold。

## 默认 retrieval_execution_mode

| 条件 | mode |
|------|------|
| structured 候选 | `structured` |
| `MESH_EMBED_ENABLED=1` 或 `MESH_VECTOR_ENABLED=1` 且已配置 | `hybrid` |
| **否则（生产默认）** | **`lexical`** |

`embeddings.enabled()` **默认 OFF**（`"0"`）。仅有 API key **不得**进入 embed。

## lexical 热路径（必须）

```
request → cheap route (Temporal/Guard/Structured)
       → FTS + ranking
       → claim / evidence（必要时 1× semantic）
       → answer（必要时 1×）
```

禁止：`embed_one` / Embedding API（含 denial 二次 `prepare`）。

## Performance Sprint v2（Answer + Semantic 双通道）

不改 Retrieval / Ranking / Claim 语义定义。仅压：

| 通道 | 默认 |
|------|------|
| Answer max_tokens | `MESH_ANSWER_MAX_TOKENS=700` |
| Answer Top-N / body | `MESH_ANSWER_CTX_N=6` · `MESH_ANSWER_BODY_CHARS=280` |
| Answer system | `qa.md` + 精简运行时约束（不再整份塞 00_base_rules） |
| Semantic max_tokens | `MESH_SEMANTIC_MAX_TOKENS=160` |
| Semantic snips | `MESH_SEMANTIC_SNIP_N=5` × `MESH_SEMANTIC_SNIP_CHARS=150` |

内部目标：普通检索 P95&lt;8s；强 Claim P95&lt;15s；简单事实不回退。
