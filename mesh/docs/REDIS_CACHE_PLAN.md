# Mesh 缓存演进：Redis 方案设计

## 1. 现状

当前 Mesh 部署为单容器、单 uvicorn worker（worker=1），因此 Batch 3 使用进程内字典缓存即可满足需求：

- `mesh/app/llm_cache.py`：LLM 响应缓存（TTL 5min，LRU 256 条）。
- `mesh/app/request_cache.py`：请求级缓存（CompanyUnderstanding、org people pool）。

限制：进程内缓存无法在多个 worker / 多个容器实例之间共享；容器重启缓存丢失。

## 2. 何时引入 Redis

建议在以下任一条件触发时引入 Redis：

1. uvicorn worker 数 > 1（多进程共享缓存）。
2. Mesh 横向扩展为多个容器实例。
3. 需要缓存持久化或跨重启保留热数据。
4. 需要更细粒度的缓存统计、命中率监控。

## 3. 分级缓存策略（L1 + L2）

```
L1：进程内缓存（命中最快，微秒级）
L2：Redis 缓存（跨进程/跨实例共享，毫秒级）
```

读取顺序：L1 → L2 → 真实调用；写入顺序：真实调用 → L2 → L1。

### 3.1 L1 进程内缓存

- 保留现有 `llm_cache.py` / `request_cache.py`。
- L1 TTL 设置比 L2 短（例如 LLM 响应 L1=5min，L2=30min）。
- L1 容量限制保持较小（256 条），避免单进程 OOM。

### 3.2 L2 Redis 缓存

- Key 命名空间：`mesh:llm:<hash>`、`mesh:req:cc:<hash>`、`mesh:req:org:<hash>`。
- Value 结构：字符串（压缩后 JSON）。
- TTL：按数据类型区分：
  - LLM 响应：30min（写密集但相对静态）。
  - CompanyUnderstanding：2min（依赖 identity/session，变化较快）。
  - org people pool：5min（通讯录变化不频繁）。

## 4. Value 压缩与序列化

| 方案 | 优点 | 缺点 |
|---|---|---|
| JSON + gzip | 可读、通用、跨语言 | 压缩比一般 |
| msgpack + gzip | 更小、更快 | 需要 msgpack 依赖 |
| pickle | 最小、Python 原生 | 不安全、跨语言困难 |

推荐：**JSON + gzip** 作为默认方案，LLM 响应文本较大，压缩收益明显。

伪代码：

```python
import gzip, json

def pack(value: dict) -> bytes:
    return gzip.compress(json.dumps(value, ensure_ascii=False).encode("utf-8"), compresslevel=6)

def unpack(raw: bytes) -> dict:
    return json.loads(gzip.decompress(raw).decode("utf-8"))
```

## 5. 缓存击穿 / 雪崩 / 穿透防护

### 5.1 缓存击穿（hot key 失效瞬间大量请求涌入）

- 互斥锁：使用 Redis `SET key value NX EX 10` 获取锁，只有一个进程重建缓存。
- 逻辑过期：缓存 value 内带 `expire_at`，在 TTL 到达前由后台异步刷新。

### 5.2 缓存雪崩（大量 key 同时失效）

- 随机 TTL：基础 TTL + 随机 jitter（±10%），避免同时失效。
- 限流/熔断：Redis 不可用时 fall back 到直接调用，避免级联故障。

### 5.3 缓存穿透（查询不存在的数据）

- 空值缓存：对查询结果为空的情况也缓存短暂时间（例如 30s）。
- 布隆过滤器：对 LLM key 的输入参数做布隆过滤，拦截明显无效的 key。

## 6. Redis 熔断降级

```python
class RedisCache:
    def __init__(self):
        self._client = None
        self._fail_count = 0
        self._fail_until = 0
        self._circuit_open = False

    def get(self, key: str):
        if self._circuit_open and time.monotonic() < self._fail_until:
            return None  # 熔断，直接回源
        try:
            value = self._client.get(key)
            self._fail_count = 0
            return value
        except Exception:
            self._fail_count += 1
            if self._fail_count >= 5:
                self._circuit_open = True
                self._fail_until = time.monotonic() + 30
            return None
```

熔断后：
- 降级到 L1 进程内缓存。
- 如果 L1 也没有，直接调用真实服务。
- 定期探测 Redis 恢复（例如每 10s 一次），恢复后关闭熔断。

## 7. 数据一致性

- LLM 响应缓存：只按 model/system/user/max_tokens/temperature/top_p 等输入参数 key，不依赖外部状态，一致性高。
- CompanyUnderstanding：依赖 identity/permission/session/org_snapshot，key 已包含这些摘要，且 TTL 较短，可接受短暂不一致。
- org people pool：依赖 roster 文件 mtime 和通讯录签名，key 包含签名，签名变化时自动失效。

## 8. 部署与运维

- Redis 实例：建议独立部署，不要与应用容器同生同灭。
- 连接池：使用 `redis-py` 连接池，设置 `max_connections=50`。
- 监控：
  - 缓存命中率（L1 / L2）。
  - Redis 内存使用、QPS、慢查询。
  - 熔断状态、降级次数。
- 告警：命中率骤降、Redis 连接失败次数突增。

## 9. 实施步骤（建议）

1. 引入 `redis-py` 依赖。
2. 封装 `mesh/app/redis_cache.py`，实现 `RedisCache` 类（get/set/delete/health_check）。
3. 在 `llm_cache.py` 中接入 L2 Redis：先查 L1，再查 L2，写入时双写。
4. 在 `company_context.py` / `person_resolve.py` 中接入 L2 Redis。
5. 添加环境变量：
   - `MESH_REDIS_URL`
   - `MESH_REDIS_TTL_LLM_S`
   - `MESH_REDIS_TTL_REQ_S`
   - `MESH_REDIS_ENABLED`
6. 配置监控与告警。
7. 灰度：先在一个实例启用，观察命中率与延迟变化。

## 10. 风险与回滚

- 风险：Redis 故障可能导致延迟上升（降级链路）。
- 回滚：设置 `MESH_REDIS_ENABLED=0` 即可切回纯进程内缓存。
- 数据安全：LLM 响应可能包含敏感信息，Redis 应启用 AUTH，部署在私有网络。
