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

## 验收钩子

- `prepared["retrieval_execution_mode"] == "lexical"`（Vector OFF）
- request profile：`embed_call_count == 0`
