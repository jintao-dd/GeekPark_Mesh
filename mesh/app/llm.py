"""GeekPark Mesh · LLM 业务层

本文件**不得 import 任何厂商 SDK**。所有模型调用经 providers 适配层，
切换模型只改 .env 里的 MESH_LLM_PROVIDER，业务代码零改动。
"""
import datetime
import json, re, threading, time, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from .providers import get_provider, LLMError
from . import llm_cache

# segment 抽取全局并发信号量：避免突发大文件同时发起大量 LLM 调用压垮上游
_SEG_EXTRACT_SEMAPHORE = threading.Semaphore(
    max(1, int(__import__("os").environ.get("MESH_SEGMENT_EXTRACT_MAX_CONCURRENT") or 4))
)
_SEG_EXTRACT_TIMEOUT_S = max(10, int(__import__("os").environ.get("MESH_SEGMENT_EXTRACT_TIMEOUT_S") or 120))

log = logging.getLogger("mesh.llm")

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

FORBIDDEN = [
    "情报", "共振", "同现", "主体", "不一致", "冲突", "缺口", "看法不同", "分歧",
    "批评", "否定", "看空", "反对", "质疑", "不认可", "该信多少", "可信度", "存疑",
    "最值得看", "最重要", "建议跟进", "可以考虑", "应该", "建议",
]
FORBIDDEN_RETRIES = 2  # 字段级修补后仍大面积命中时，整份重写的次数上限
FIELD_REWRITE_ROUNDS = 3  # 每轮扫描并改写所有命中字段
FULL_REGEN_FIELD_THRESHOLD = 8  # 命中字段超过此数 → 优先整份重写

_FORBIDDEN_RETRY_HINT = (
    "\n\n【硬性禁令】输出仍含禁用词：{words}。"
    "请整份重写 JSON；这些词在任何字段都绝对禁止（含引号内），须换说法。"
    "{snips}"
)

_REWRITE_SYSTEM = (
    "你是极客公园 Mesh 的措辞合规编辑。"
    "任务：只改写给定文案，保持事实与含义，替换所有禁用词。"
    "禁用词绝不可出现："
    + "、".join(FORBIDDEN)
    + "。可用：认为、判断、已接触、待核对、各有判断、已联动等。"
)

# Process-wide token/retry accum for Ask Baseline (Ask analysis uses a thread pool).
_usage_lock = threading.Lock()
_usage_acc = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "n_calls": 0,
    "n_retries": 0,
}


def reset_usage_accum() -> None:
    with _usage_lock:
        _usage_acc["prompt_tokens"] = 0
        _usage_acc["completion_tokens"] = 0
        _usage_acc["total_tokens"] = 0
        _usage_acc["n_calls"] = 0
        _usage_acc["n_retries"] = 0


def take_usage_accum() -> dict:
    """Snapshot + reset. Prefer total_tokens when providers send it."""
    with _usage_lock:
        pt = int(_usage_acc.get("prompt_tokens") or 0)
        ct = int(_usage_acc.get("completion_tokens") or 0)
        tt = int(_usage_acc.get("total_tokens") or 0)
        nc = int(_usage_acc.get("n_calls") or 0)
        nr = int(_usage_acc.get("n_retries") or 0)
        out = {
            "prompt_tokens": pt or None,
            "completion_tokens": ct or None,
            "total_tokens": tt or (pt + ct) or None,
            "n_calls": nc or None,
            # 0 retries is meaningful once we actually called the LLM
            "n_retries": nr if nc else None,
        }
        _usage_acc["prompt_tokens"] = 0
        _usage_acc["completion_tokens"] = 0
        _usage_acc["total_tokens"] = 0
        _usage_acc["n_calls"] = 0
        _usage_acc["n_retries"] = 0
    return {k: v for k, v in out.items() if v is not None}


def _accum_usage(usage: dict | None, *, attempts: int = 1) -> None:
    with _usage_lock:
        _usage_acc["n_calls"] = int(_usage_acc.get("n_calls") or 0) + 1
        # attempts>1 means this call is a retry of a previous attempt in the same call()
        if attempts > 1:
            _usage_acc["n_retries"] = int(_usage_acc.get("n_retries") or 0) + 1
        if not isinstance(usage, dict):
            return
        if usage.get("prompt_tokens") is not None:
            _usage_acc["prompt_tokens"] = int(_usage_acc.get("prompt_tokens") or 0) + int(usage["prompt_tokens"])
        if usage.get("completion_tokens") is not None:
            _usage_acc["completion_tokens"] = int(_usage_acc.get("completion_tokens") or 0) + int(
                usage["completion_tokens"]
            )
        if usage.get("total_tokens") is not None:
            _usage_acc["total_tokens"] = int(_usage_acc.get("total_tokens") or 0) + int(usage["total_tokens"])
        elif usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None:
            _usage_acc["total_tokens"] = int(_usage_acc.get("total_tokens") or 0) + int(
                usage.get("prompt_tokens") or 0
            ) + int(usage.get("completion_tokens") or 0)

# 任务→模型解析结果的进程内缓存（DB 选择可能随时被后台改，TTL 要短）。
# 后台改完会调 model_settings_cache_bust() 让本进程立即失效；其它 worker 靠 TTL 收敛。
_MODEL_CACHE: dict[str, tuple[float, str | None]] = {}
_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE_TTL_S = max(1.0, float(__import__("os").environ.get("MESH_MODEL_CACHE_TTL_S") or 3))


def model_settings_cache_bust() -> None:
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


def _resolve_model_for_task(task: str) -> str | None:
    """DB 里的任务选择优先（后台「模型」页可切换）；未设置则回落 .env。"""
    from . import model_settings

    t = (task or "default").strip().lower()
    try:
        from . import db

        con = db.connect()
        try:
            return model_settings.effective_model(con, t) or None
        finally:
            con.close()
    except Exception:
        # DB 不可用：绝不因此让业务断掉，直接走 env 兜底
        return model_settings.env_default_model(t) or None


def model_for_task(task: str = "default") -> str | None:
    """任务级固定模型配置（非动态 router）。

    解析顺序：**DB 任务选择（后台「模型」页）** → 环境变量兜底。
    环境变量（可选）：
      MESH_LLM_MODEL_SEMANTIC    — Claim semantic judge
      MESH_LLM_MODEL_ANSWER      — Answer 成文
      MESH_LLM_MODEL_SENSITIVE   — 敏感/对外成文（未设则回落 ANSWER / MODEL）
      MESH_LLM_MODEL_CONTROLLER  — Colleague Controller（未设则回落 SEMANTIC / MODEL）
      MESH_LLM_MODEL             — 默认回落
    """
    t = (task or "default").strip().lower()
    now = time.monotonic()
    with _MODEL_CACHE_LOCK:
        hit = _MODEL_CACHE.get(t)
        if hit and now - hit[0] <= _MODEL_CACHE_TTL_S:
            return hit[1]
    model = _resolve_model_for_task(t)
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[t] = (now, model)
    return model


def provider_info() -> dict:
    """后台"规则提示词"页显示当前模型配置，便于换模型后核对。"""
    try:
        info = get_provider().describe()
        info["task_models"] = {
            "semantic": model_for_task("semantic"),
            "answer": model_for_task("answer"),
            "sensitive": model_for_task("sensitive"),
            "controller": model_for_task("controller"),
            "default": model_for_task("default"),
        }
        return info
    except Exception as e:
        return {"provider": "?", "model": "?", "configured": False, "error": str(e)}


def budget(reserve: int = 20000) -> int:
    """按当前 provider 的上下文窗口给出单次可投喂的字符预算。

    切分策略必须读它，**不许写死"一次塞完"**——换模型时窗口差异很大。
    粗略按 1 token ≈ 1.6 中文字符估算。
    """
    try:
        win = get_provider().context_window
    except Exception:
        win = 128_000
    return max(8000, int((win - reserve) * 1.6))

_prompt_cache: dict[str, str] = {}
_prompt_cache_lock = threading.Lock()


def load_prompt(name: str) -> str:
    """读取 prompts/*.md；带进程内缓存，避免每次从磁盘读取。"""
    with _prompt_cache_lock:
        if name in _prompt_cache:
            return _prompt_cache[name]
    p = PROMPT_DIR / f"{name}.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    with _prompt_cache_lock:
        _prompt_cache[name] = text
    return text

def call(
    system: str,
    user: str,
    max_tokens: int = 4000,
    json_mode: bool = True,
    *,
    task: str = "default",
):
    """模型调用。json_mode 下解析失败会自动再请求一次。task 选固定任务模型配置。"""
    last_err = None
    provider = get_provider(model=model_for_task(task))
    model = model_for_task(task)

    # 尝试命中缓存（仅非 json_mode，避免重试语义和缓存污染）
    if not json_mode and model:
        cached = llm_cache.get_cached(
            model=model, task=task, system=system, user=user, max_tokens=max_tokens
        )
        if cached is not None:
            logging.getLogger("uvicorn.error").info(
                "llm.call cache_hit task=%s model=%s prompt_chars=%s max_tokens=%s",
                task,
                model,
                len(system) + len(user),
                max_tokens,
            )
            return cached.get("content") or ""

    # 方案 B 端到端真流式：仅 answer 任务、非 json、有活跃回调、provider 支持 stream 时启用。
    # 逐 chunk 推给飞书打字机；同时把全文攒起来返回，后续清洗/证据校验照常在全文上做。
    if (
        not json_mode
        and task == "answer"
        and hasattr(provider, "stream")
    ):
        try:
            from .agent import answer_stream
        except Exception:
            answer_stream = None
        if answer_stream is not None and answer_stream.stream_active():
            t0 = time.monotonic()
            acc: list[str] = []
            try:
                for chunk in provider.stream(system, user, max_tokens=max_tokens, task=task):
                    if not chunk:
                        continue
                    acc.append(chunk)
                    answer_stream.emit_delta(chunk, "".join(acc))
                text = "".join(acc)
            finally:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logging.getLogger("uvicorn.error").info(
                    "llm.call stream task=%s model=%s elapsed_ms=%s prompt_chars=%s max_tokens=%s chars=%s",
                    task, model, elapsed_ms, len(system) + len(user), max_tokens, len(text),
                )
            # 流式路径不写缓存（usage 无法从 stream 精确获取；保持与缓存 key 语义一致由非流式负责）
            if text:
                return text
            # 流式空结果 → 落回非流式重试一次
            log.warning("llm.call stream empty; fallback to non-stream")

    for attempt in range(2 if json_mode else 1):
        t0 = time.monotonic()
        try:
            if hasattr(provider, "complete_detail"):
                detail = provider.complete_detail(system, user, max_tokens=max_tokens, task=task)
                text = detail.get("content") or ""
                _accum_usage(detail.get("usage"), attempts=attempt + 1)
            else:
                text = provider.complete(system, user, max_tokens=max_tokens)
                _accum_usage(None, attempts=attempt + 1)
        finally:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logging.getLogger("uvicorn.error").info(
                "llm.call task=%s model=%s attempt=%s elapsed_ms=%s prompt_chars=%s max_tokens=%s",
                task,
                model,
                attempt,
                elapsed_ms,
                len(system) + len(user),
                max_tokens,
            )
        if not json_mode:
            # 缓存低风险任务结果
            if model and hasattr(provider, "complete_detail"):
                llm_cache.set_cached(
                    model=model,
                    task=task,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    result=detail or {"content": text},
                )
            return text
        try:
            return parse_json(text)
        except Exception as e:
            last_err = e
            if attempt == 0:
                # 第二次加硬约束，降低坏 JSON 概率
                user = user + "\n\n【重要】上一次输出不是合法 JSON。请只输出一个合法 JSON 对象，不要 markdown 代码块，不要尾逗号，不要注释。"
                continue
    raise LLMError(f"模型返回的 JSON 无法解析：{last_err}")

def _repair_json_text(t: str) -> str:
    """修常见坏 JSON：代码围栏、尾逗号、智能引号。"""
    t = t.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    # 去掉对象/数组里的尾逗号： ,}  ,]
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t.strip()

def parse_json(text: str):
    t = _repair_json_text(text)
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", t)
        if not m:
            raise
        raw = _repair_json_text(m.group(1))
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 截断类：尽量补齐括号后再试
            open_curly = raw.count("{") - raw.count("}")
            open_square = raw.count("[") - raw.count("]")
            patched = raw + ("]" * max(0, open_square)) + ("}" * max(0, open_curly))
            patched = _repair_json_text(patched)
            return json.loads(patched)

def forbidden_hits(text: str) -> list[str]:
    return [w for w in FORBIDDEN if w in (text or "")]


def forbidden_snippet(text: str, word: str, radius: int = 28) -> str:
    """返回禁用词附近片段，便于编辑定位。"""
    t = text or ""
    i = t.find(word)
    if i < 0:
        return ""
    snip = t[max(0, i - radius) : i + len(word) + radius]
    return re.sub(r"\s+", " ", snip).strip()


def _forbidden_retry_suffix(payload: str) -> str:
    hits = forbidden_hits(payload)
    if not hits:
        return ""
    snips = [forbidden_snippet(payload, w) for w in hits[:4]]
    snip_txt = "；".join(s for s in snips if s)
    return _FORBIDDEN_RETRY_HINT.format(
        words="、".join(hits),
        snips=("问题片段：" + snip_txt) if snip_txt else "",
    )


def _iter_string_paths(obj, prefix: str = ""):
    if isinstance(obj, str):
        if prefix:
            yield prefix, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield from _iter_string_paths(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}.{i}"
            yield from _iter_string_paths(v, p)


def _get_by_path(obj, path: str):
    ref = obj
    for p in path.split("."):
        ref = ref[int(p)] if p.isdigit() else ref[p]
    return ref


def _set_by_path(obj, path: str, value: str) -> None:
    parts = path.split(".")
    ref = obj
    for p in parts[:-1]:
        ref = ref[int(p)] if p.isdigit() else ref[p]
    last = parts[-1]
    if isinstance(ref, list):
        ref[int(last)] = value
    else:
        ref[last] = value


def _context_for_path(obj, path: str) -> str:
    try:
        parent = _get_by_path(obj, ".".join(path.split(".")[:-1])) if "." in path else obj
        return json.dumps(parent, ensure_ascii=False)[:1800]
    except Exception:
        return ""


def _forbidden_field_paths(obj) -> list[tuple[str, str, list[str]]]:
    out = []
    for path, text in _iter_string_paths(obj):
        hits = forbidden_hits(text)
        if hits:
            out.append((path, text, hits))
    return out


def _rewrite_text_field(text: str, context: str, words: list[str]) -> str:
    user = (
        f"命中禁用词：{'、'.join(words)}\n\n"
        f"【上下文（仅供理解，勿照抄）】\n{context or '（无）'}\n\n"
        f"【待改写】\n{text}\n\n"
        "只输出改写后的正文，不要解释、不要 markdown、不要 JSON。"
    )
    out = call(_REWRITE_SYSTEM, user, max_tokens=900, json_mode=False)
    cleaned = re.sub(r"\s+", " ", (out or "").strip())
    return cleaned


def polish_forbidden_fields(obj):
    """只改写命中禁用词的字符串字段，结构与其余文案不动。

    优化：同一轮内多个命中的字段互相独立，使用线程池并行改写。
    """
    for _ in range(FIELD_REWRITE_ROUNDS):
        batch = _forbidden_field_paths(obj)
        if not batch:
            return obj
        if len(batch) == 1:
            path, text, words = batch[0]
            ctx = _context_for_path(obj, path)
            new_text = _rewrite_text_field(text, ctx, words)
            if new_text and not forbidden_hits(new_text):
                _set_by_path(obj, path, new_text)
            continue

        # 并行改写多个字段
        results = []
        max_workers = min(4, len(batch))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {
                pool.submit(_rewrite_text_field, text, _context_for_path(obj, path), words): (path, text)
                for path, text, words in batch
            }
            for fut in as_completed(futs):
                path, original_text = futs[fut]
                try:
                    new_text = fut.result()
                except Exception:
                    continue
                if new_text and not forbidden_hits(new_text):
                    results.append((path, new_text))
                else:
                    # 改写失败保留原文，避免污染
                    results.append((path, original_text))
        # 按原顺序写回，保证确定性
        for path, new_text in results:
            _set_by_path(obj, path, new_text)
    return obj


def call_json_compliant(system: str, user: str, max_tokens: int = 4000) -> dict:
    """JSON 生成：先出稿 → 字段级合规改写 → 大面积命中再整份重写。"""
    data = call(system, user, max_tokens=max_tokens)
    batch = _forbidden_field_paths(data)
    if len(batch) > FULL_REGEN_FIELD_THRESHOLD:
        for _ in range(FORBIDDEN_RETRIES):
            suffix = _forbidden_retry_suffix(json.dumps(data, ensure_ascii=False))
            if not suffix:
                break
            data = call(system, user + suffix, max_tokens=max_tokens)
            if len(_forbidden_field_paths(data)) <= FULL_REGEN_FIELD_THRESHOLD:
                break
    data = polish_forbidden_fields(data)
    if _forbidden_field_paths(data):
        suffix = _forbidden_retry_suffix(json.dumps(data, ensure_ascii=False))
        if suffix:
            data = call(system, user + suffix, max_tokens=max_tokens)
            data = polish_forbidden_fields(data)
    return data

_TEAM_OVERLAYS = {
    "T3": "team_svbd",
    "T10": "team_gp",
    "T11": "team_video",
    "T12": "team_podcast",
}


# ---------- 1. 抽取：单个来源 → 条目 ----------
def extract_items(
    stype: str,
    team: str,
    title: str,
    text: str,
    *,
    period_start: str = "",
    period_end: str = "",
    period_label: str = "",
    channel: str = "manual",
) -> list[dict]:
    from .aggregator import sanitize_owner_team

    system = (
        load_prompt("00_base_rules")
        + "\n\n"
        + load_prompt("05_owner_attrib")
        + "\n\n"
        + load_prompt(f"extract_{stype}")
    )
    overlay = _TEAM_OVERLAYS.get(stype, "")
    if overlay:
        extra = load_prompt(overlay)
        if extra:
            system += "\n\n" + extra
    system += "\n\n" + load_prompt("90_output_items")
    lim = budget()
    if period_start and period_end:
        period_line = f"本期区间：{period_start} 至 {period_end}"
        if period_label:
            period_line += f"（{period_label}）"
    elif period_start or period_end:
        period_line = f"本期区间：{period_start or period_end}"
    else:
        period_line = "本期区间：未指定"
    ch = (channel or "manual").strip()
    ch_hint = "聚合器（混合多团队，逐条写 owner_team）" if ch == "aggregator" else "部门提交（整份默认归提交团队，混合内容仍逐条判定）"
    user = (
        f"数据类型：{stype}\n团队：{team}\n采集通道：{ch_hint}\n文档标题：{title}\n{period_line}\n\n"
        f"【原文开始】\n{text[:lim]}\n【原文结束】\n\n"
        "请按输出格式返回 JSON。每条 text 不得含全局禁用词，原文有也必须换说法。"
        "涉及「只抽本期」的类型（T10/T11/T12 等）须严格按本期区间过滤。"
    )
    data = call_json_compliant(system, user, max_tokens=8000)
    items = data.get("items", []) if isinstance(data, dict) else data
    out = []
    for it in items:
        zone = int(it.get("zone", 4) or 4); level = str(it.get("level", "L1")).upper()
        blocked = 1 if (zone == 5 or level == "L3") else 0
        llm_hint = sanitize_owner_team(it.get("owner_team"))
        raw = (it.get("raw_snippet") or it.get("text") or "").strip()
        text = (it.get("text") or "").strip()
        if not raw and text:
            raw = text
        out.append({"zone": zone, "level": level, "kind": it.get("kind", "fact"), "text": text,
                    "raw_snippet": raw,
                    "entities": it.get("entities", []), "roles": it.get("roles", []), "signals": it.get("signals", []),
                    "llm_owner_team_hint": llm_hint,
                    "source_label": it.get("source_label", ""), "pointer": it.get("pointer", ""), "blocked": blocked, "team": it.get("team") or team})
    # 名单打包 → 一主体一条；⑤区父条不拆；子条仍走硬拦
    from .item_list_split import expand_stage_list_items
    from .zone_hard import apply_hard_blocks
    return apply_hard_blocks(expand_stage_list_items(out))


def extract_source(
    stype: str,
    team: str,
    title: str,
    text: str,
    *,
    period_start: str = "",
    period_end: str = "",
    period_label: str = "",
    channel: str = "manual",
    skip_split: bool = False,
    source_id: int | None = None,
    prior_items: list[dict] | None = None,
) -> tuple[list[dict], dict | None]:
    """单个来源抽取：聚合包自动拆段并按段调用 extract_T*.md。

    返回 (items, split_meta)。split_meta 仅混合包有值，供 sources.meta 落库与 UI 展示。
    上传已预拆（skip_split=True）时按单段抽取，避免二次拆分。

    prior_items：上轮同 source 的条目（含 §sd:digest| pointer）。段 digest 未变则复用，不调 LLM。
    """
    from .aggregator import should_split, split_bundle_ex
    from .owner_guard import apply_item_owner_guards
    from .segment_cache import (
        build_segment_digests_meta,
        clone_item_for_reuse,
        group_items_by_segment_digest,
        segment_digest,
        stamp_segment_digest,
    )

    def _stamp(items: list[dict]) -> list[dict]:
        if source_id is None:
            return items
        for it in items:
            it["source_id"] = source_id
        return items

    if (
        not skip_split
        and (stype == "T13" or should_split(stype=stype, team=team, channel=channel, title=title, text=text))
    ):
        split = split_bundle_ex(text, source_title=title or "")
        if not split.segments:
            msg = "；".join(split.warnings) if split.warnings else "正文为空或无可抽取段落"
            raise ValueError(f"无法抽取：{msg}")
        ch = (channel or "aggregator").strip() or "aggregator"
        prior_by = group_items_by_segment_digest(prior_items or [])
        out: list[dict] = []
        reused = 0
        extracted_n = 0

        # 先分离复用段和需要 LLM 抽取的段
        to_extract: list[tuple[Any, str]] = []  # (segment, digest)
        for seg in split.segments:
            dig = segment_digest(stype=seg.stype, title=seg.title, text=seg.text or "")
            if dig in prior_by and prior_by[dig]:
                for old in prior_by[dig]:
                    it = clone_item_for_reuse(old, digest=dig)
                    it["item_stype"] = seg.stype
                    it["_segment_team"] = seg.owner_hint or seg.team
                    out.append(it)
                reused += 1
            else:
                to_extract.append((seg, dig))

        # 并行抽取独立段落：I/O-bound，受全局信号量 + 线程池双重限流
        if to_extract:
            import os
            max_workers = max(1, min(len(to_extract), int(os.environ.get("MESH_SEGMENT_EXTRACT_WORKERS") or 4)))

            def _extract_seg(args: tuple[Any, str]) -> list[dict]:
                seg, dig = args
                seg_title = f"{title or '数据聚合'} · {seg.title}"[:200]
                # 全局信号量：限制跨请求的同时 LLM 抽取调用数
                if not _SEG_EXTRACT_SEMAPHORE.acquire(timeout=_SEG_EXTRACT_TIMEOUT_S):
                    log.warning("segment extract semaphore timeout stype=%s title=%s", seg.stype, seg.title[:40])
                    return []
                try:
                    items = extract_items(
                        seg.stype, seg.team, seg_title, seg.text,
                        period_start=period_start, period_end=period_end,
                        period_label=period_label, channel=ch,
                    )
                except Exception:
                    items = []
                finally:
                    _SEG_EXTRACT_SEMAPHORE.release()
                for it in items:
                    it["item_stype"] = seg.stype
                    it["_segment_team"] = seg.owner_hint or seg.team
                    stamp_segment_digest(it, dig)
                return items

            if len(to_extract) == 1 or max_workers == 1:
                items = _extract_seg(to_extract[0])
                seg, dig = to_extract[0]
                for it in items:
                    it["item_stype"] = seg.stype
                    it["_segment_team"] = seg.owner_hint or seg.team
                    out.append(stamp_segment_digest(it, dig))
                extracted_n += 1
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futs = {pool.submit(_extract_seg, args): args for args in to_extract}
                    for fut in as_completed(futs):
                        seg, dig = futs[fut]
                        try:
                            # 子任务整体超时：包含排队 + LLM 调用
                            items = fut.result(timeout=_SEG_EXTRACT_TIMEOUT_S)
                        except Exception:
                            items = []
                        for it in items:
                            it["item_stype"] = seg.stype
                            it["_segment_team"] = seg.owner_hint or seg.team
                            out.append(stamp_segment_digest(it, dig))
                        extracted_n += 1

        meta = split.to_meta()
        meta["segment_digests"] = build_segment_digests_meta(split.segments)
        meta["segment_reuse"] = {"reused": reused, "extracted": extracted_n, "total": len(split.segments)}
        return apply_item_owner_guards(_stamp(out)), meta
    items = extract_items(
        stype, team, title, text,
        period_start=period_start, period_end=period_end,
        period_label=period_label, channel=channel,
    )
    if skip_split:
        for it in items:
            it["_segment_team"] = team
        return apply_item_owner_guards(_stamp(items)), None
    # 非拆段路径也跑归属守卫，避免同 pointer 冲突双挂
    return apply_item_owner_guards(_stamp(items)), None


def split_needs_review(
    split_meta: dict | None,
    *,
    stype: str = "",
    team: str = "",
    channel: str = "",
) -> bool:
    """仅内容聚合包在拆段置信度低时要求人工确认；单团队来源不拦。

    目标：少打扰人——只有 low 置信才拦；medium/high 自动过。
    """
    from .ingest import is_aggregation_source
    from .aggregator import split_confidence

    if not is_aggregation_source(stype=stype, team=team, channel=channel):
        return False
    if not split_meta:
        return False
    mode = (split_meta.get("mode") or "").strip()
    if mode == "pre_split":
        return False

    conf = (split_meta.get("confidence") or "").strip() or split_confidence(split_meta)
    return conf == "low"

# ---------- 2. 团队要点卡 ----------
def build_team_card(team: str, items: list[dict], period: str) -> dict:
    system = load_prompt("00_base_rules") + "\n\n" + load_prompt("card_team")
    user = (
        f"团队：{team}\n期：{period}\n"
        f"以下是已通过硬拦（不含⑤区与L3）的条目 JSON：\n{json.dumps(items, ensure_ascii=False)[:budget()]}\n\n"
        "请生成该团队的要点卡 JSON。全文字段不得含全局禁用词。"
    )
    return call_json_compliant(system, user, max_tokens=4000)

# ---------- 3. 周报草稿 ----------
def build_issue_draft(
    period: dict,
    team_cards: list[dict],
    relation_candidates: list[dict],
    first_names: list[str],
    external_items: list[dict],
    prev_issue_summary: str,
) -> dict:
    system = (
        load_prompt("00_base_rules")
        + "\n\n"
        + load_prompt("issue_draft")
        + "\n\n"
        + load_prompt("issue_draft_candidates")
        + "\n\n"
        + load_prompt("91_output_issue")
    )
    user = (
        f"期号：{period['slug']}；区间：{period['date_start']} 至 {period['date_end']}；"
        f"显示：{period['period_label']}\n"
        f"归档比对得出的『本期首次进入记录的公司/人/话题』候选："
        f"{json.dumps(first_names, ensure_ascii=False)}\n"
        f"上一期摘要（避免重复、便于比较）：{prev_issue_summary[:3000]}\n\n"
        f"【各团队要点卡 team_cards】\n{json.dumps(team_cards, ensure_ascii=False)[:budget(35000)]}\n\n"
        f"【跨团队关系候选 relation_candidates（relations 节只能用这些）】\n"
        f"{json.dumps(relation_candidates, ensure_ascii=False)[:budget(20000)]}\n\n"
        f"【外部媒体条目（只用于 gaps / 弱关系背景，勿写入 relations）】\n"
        f"{json.dumps(external_items, ensure_ascii=False)[:15000]}\n\n"
        "请输出完整周报 JSON。**relations 必须为空数组 []**（关系卡由独立两阶段流程生成）。"
        "全篇任何字段都不得出现全局禁用词；关键词墙 rows 也必须换说法，禁止照抄条目原话。"
    )
    return call_json_compliant(system, user, max_tokens=16000)


RELATION_DECISIONS_MAX_TOKENS = 8000
RELATION_DECISIONS_RETRIES = 3
# mco-6 / 较弱模型在单次 JSON 里约 80+ 条会截断；分批 + 漏答只补 missing。
RELATION_DECISIONS_BATCH_SIZE = 40


class RelationDecisionCoverageError(LLMError):
    """LLM 未对全部 candidate 返回 decision。"""

    def __init__(
        self,
        message: str,
        *,
        missing_ids: list[str],
        meta: dict | None = None,
        decisions: list[dict] | None = None,
    ):
        super().__init__(message)
        self.missing_ids = list(missing_ids)
        self.meta = dict(meta or {})
        self.decisions = list(decisions or [])


def prepare_relation_decisions_messages(
    candidates: list[dict],
    team_cards: list[dict],
    *,
    extra_user_suffix: str = "",
) -> tuple[str, str, dict]:
    system = (
        load_prompt("00_base_rules")
        + "\n\n"
        + load_prompt("issue_relation_decisions")
        + "\n\n"
        '输出严格 JSON：{"relation_decisions":[...]}'
    )
    slim = []
    for c in candidates:
        slim.append({
            "candidate_id": c.get("candidate_id"),
            "title": c.get("title"),
            "teams": c.get("teams"),
            "suggested_label": c.get("suggested_label"),
            "team_facts": [
                {
                    "team": tf.get("team"),
                    "item_ids": tf.get("item_ids"),
                    "snippets": (tf.get("snippets") or [])[:2],
                }
                for tf in (c.get("team_facts") or [])
            ],
        })
    user_budget = budget(18000)
    cards_budget = budget(8000)
    slim_json = json.dumps(slim, ensure_ascii=False)
    cards_json = json.dumps(team_cards, ensure_ascii=False)
    user = (
        f"【relation_candidates】\n{slim_json[:user_budget]}\n\n"
        f"【team_cards 摘要】\n{cards_json[:cards_budget]}\n\n"
        "对每个 candidate_id 输出一条 relation_decisions；禁止 title/body/details/teams/sources。"
        f"{extra_user_suffix}"
    )
    meta = {
        "n_candidates": len(candidates),
        "candidate_ids": [c.get("candidate_id") for c in candidates],
        "system_chars": len(system),
        "user_chars": len(user),
        "slim_json_chars": len(slim_json),
        "slim_json_truncated": len(slim_json) > user_budget,
        "team_cards_json_chars": len(cards_json),
        "team_cards_truncated": len(cards_json) > cards_budget,
        "max_tokens": RELATION_DECISIONS_MAX_TOKENS,
    }
    return system, user, meta


def missing_decision_ids(candidates: list[dict], decisions: list[dict]) -> list[str]:
    expected = [
        (c.get("candidate_id") or "").strip()
        for c in candidates
        if (c.get("candidate_id") or "").strip()
    ]
    got: set[str] = set()
    for d in decisions:
        if not isinstance(d, dict):
            continue
        cid = (d.get("candidate_id") or "").strip()
        if cid:
            got.add(cid)
    return [cid for cid in expected if cid not in got]


def _merge_relation_decisions(
    existing: list[dict],
    batch: list[dict],
    preferred_ids: list[str] | None = None,
) -> list[dict]:
    by_id: dict[str, dict] = {}
    for d in existing:
        cid = (d.get("candidate_id") or "").strip()
        if cid:
            by_id[cid] = d
    for d in batch:
        cid = (d.get("candidate_id") or "").strip()
        if cid:
            by_id[cid] = d
    if preferred_ids is None:
        preferred_ids = list(by_id.keys())
    out = [by_id[cid] for cid in preferred_ids if cid in by_id]
    seen = {d.get("candidate_id") for d in out}
    for d in batch:
        cid = (d.get("candidate_id") or "").strip()
        if cid and cid not in seen:
            out.append(d)
            seen.add(cid)
    return out


def _call_relation_decisions_batch(
    candidates: list[dict],
    team_cards: list[dict],
    *,
    extra_user_suffix: str = "",
) -> tuple[list[dict], dict]:
    system, user, prep_meta = prepare_relation_decisions_messages(
        candidates, team_cards, extra_user_suffix=extra_user_suffix,
    )
    out = call_json_compliant(system, user, max_tokens=RELATION_DECISIONS_MAX_TOKENS)
    batch = [d for d in (out.get("relation_decisions") or []) if isinstance(d, dict)]
    return batch, prep_meta


def build_relation_decisions(
    candidates: list[dict],
    team_cards: list[dict],
) -> list[dict]:
    decisions, _meta = build_relation_decisions_with_coverage(candidates, team_cards)
    return decisions


def build_relation_decisions_with_coverage(
    candidates: list[dict],
    team_cards: list[dict],
    *,
    max_retries: int = RELATION_DECISIONS_RETRIES,
    batch_size: int = RELATION_DECISIONS_BATCH_SIZE,
) -> tuple[list[dict], dict]:
    """带 coverage 校验与 retry；仍缺则抛 RelationDecisionCoverageError。

    - 候选数超过 batch_size 时分批调用，避免单次 JSON 截断。
    - 漏答重试只送 missing 子集（不再塞全量），补全成功率更高。
    """
    attempts: list[dict] = []
    all_ids = [
        (c.get("candidate_id") or "").strip()
        for c in candidates
        if (c.get("candidate_id") or "").strip()
    ]
    by_cand = {
        (c.get("candidate_id") or "").strip(): c
        for c in candidates
        if (c.get("candidate_id") or "").strip()
    }
    decisions: list[dict] = []
    bs = max(1, int(batch_size or RELATION_DECISIONS_BATCH_SIZE))

    # Pass 0: batched first coverage
    chunks = [candidates[i : i + bs] for i in range(0, len(candidates), bs)] or [[]]
    for bi, chunk in enumerate(chunks):
        if not chunk:
            continue
        batch, prep_meta = _call_relation_decisions_batch(chunk, team_cards)
        decisions = _merge_relation_decisions(decisions, batch, preferred_ids=all_ids)
        missing = missing_decision_ids(candidates, decisions)
        attempts.append({
            "attempt": 0,
            "batch_index": bi,
            "n_batch_candidates": len(chunk),
            "n_returned": len(batch),
            "n_merged": len(decisions),
            "missing_ids": list(missing),
            **prep_meta,
        })

    missing = missing_decision_ids(candidates, decisions)

    # Pass 1..N: missing-only retries
    for attempt in range(1, max_retries + 1):
        if not missing:
            break
        miss_cands = [by_cand[cid] for cid in missing if cid in by_cand]
        # 漏答也分批，防止补全时再次截断
        miss_chunks = [miss_cands[i : i + bs] for i in range(0, len(miss_cands), bs)]
        for mi, chunk in enumerate(miss_chunks):
            suffix = (
                f"\n\n【硬性补全】以下 candidate_id 必须各输出一条 relation_decisions，"
                f"不得遗漏：{', '.join((c.get('candidate_id') or '').strip() for c in chunk)}。"
                f"本批仅 {len(chunk)} 条，请完整输出。"
            )
            batch, prep_meta = _call_relation_decisions_batch(
                chunk, team_cards, extra_user_suffix=suffix,
            )
            decisions = _merge_relation_decisions(decisions, batch, preferred_ids=all_ids)
            missing = missing_decision_ids(candidates, decisions)
            attempts.append({
                "attempt": attempt,
                "batch_index": mi,
                "n_batch_candidates": len(chunk),
                "n_returned": len(batch),
                "n_merged": len(decisions),
                "missing_ids": list(missing),
                "missing_only": True,
                **prep_meta,
            })
        missing = missing_decision_ids(candidates, decisions)

    meta = {
        "n_candidates": len(candidates),
        "n_decisions_received": len(decisions),
        "missing_ids": missing,
        "attempts": attempts,
        "retries_used": max(0, len([a for a in attempts if a.get("attempt", 0) > 0])),
        "batch_size": bs,
        "coverage_ok": not missing,
    }
    if missing:
        raise RelationDecisionCoverageError(
            f"LLM 漏答 {len(missing)} 条 relation_decisions：{', '.join(missing)}",
            missing_ids=missing,
            meta=meta,
            decisions=decisions,
        )
    return decisions, meta


def build_relation_narratives(locked_relations: list[dict]) -> list[dict]:
    """兼容旧接口 → Relation Writing Module。"""
    from .relation_writer import call_writer_llm

    return call_writer_llm(locked_relations)

# ---------- 4. AI 问答 ----------
# Performance Sprint v2 · Answer 通道（不改 Answer Contract / 业务语义）
# 默认压低 completion + 精简送给模型的记录；可用环境变量调参。
def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    import os

    try:
        v = int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def answer_max_tokens() -> int:
    return _env_int("MESH_ANSWER_MAX_TOKENS", 700, lo=200, hi=2000)


def answer_ctx_limit() -> int:
    return _env_int("MESH_ANSWER_CTX_N", 6, lo=3, hi=12)


def answer_body_chars() -> int:
    return _env_int("MESH_ANSWER_BODY_CHARS", 280, lo=40, hi=600)


def pack_answer_contexts(contexts: list[dict] | None, *, limit: int | None = None) -> list[dict]:
    """Answer 专用：去重、裁剪字段与正文，降低 prompt tokens（不改检索结果本身）。"""
    n = answer_ctx_limit() if limit is None else limit
    body_n = answer_body_chars()
    out: list[dict] = []
    seen: set[str] = set()
    for c in contexts or []:
        if not isinstance(c, dict):
            continue
        sec = str(c.get("章节") or "")
        if sec in ("检索范围", "查询说明", "检索说明"):
            continue
        issue = str(c.get("期号") or c.get("issue") or "").strip()
        title = str(c.get("标题") or c.get("title") or "").strip()
        body = str(c.get("内容") or c.get("body") or c.get("snippet") or "").strip()
        key = f"{issue}|{title}|{body[:48]}"
        if key in seen:
            continue
        seen.add(key)
        packed = {
            "期号": issue,
            "章节": sec,
            "标题": title,
            "内容": body[:body_n],
        }
        src = c.get("来源层") or c.get("source_label")
        if src:
            packed["来源层"] = str(src)[:40]
        out.append(packed)
        if len(out) >= n:
            break
    return out


_ANSWER_RUNTIME_RULES = (
    "【成文约束】只根据给出的可用记录回答；禁止编造。"
    "禁用词：" + "、".join(FORBIDDEN[:12]) + " 等（见系统规则）。"
    "内部同事不写人名；⑤区/L3 内容说明不在分发范围。"
    "输出结构：先结论，再必要说明，末尾「来源」列出期号·章节·标题；勿写长分析或分点散文。"
)


def _qa_prompt(
    question: str,
    contexts: list[dict],
    mode: str = "lexical",
    history: list[dict] | None = None,
    temporal_block: str = "",
) -> tuple[str, str]:
    # Runtime：Answer 用 qa 规则 + 精简运行时约束，不再整份塞入抽取用 00_base_rules
    # （业务语义仍以 qa.md + Answer Contract 为准；Forbidden 列表与代码侧一致）
    system = (load_prompt("qa") or "").strip() + "\n\n" + _ANSWER_RUNTIME_RULES
    mode_hint = (
        "本轮上下文来自「主体×团队」结构化查询（差集/交集/聚合），列表即全部命中结果；不要补充未列出的公司或人。"
        if mode == "structured"
        else (
            "本轮上下文来自混合检索（全文 + 条目索引"
            + (" + 向量语义" if mode == "hybrid" else "")
            + "），可能不完整。"
        )
    )
    hist_block = ""
    if history:
        hist_block = (
            "\n\n此前对话（同一用户/群/话题线程，供指代消解；仍以本轮「可用记录」为准）：\n"
            + json.dumps(history[-4:], ensure_ascii=False)[:2000]
        )
    today = datetime.date.today().isoformat()
    time_anchor = (
        f"今天（回答参照日）是 {today}。"
        "记录里的「本周/明天/昨天/近日」相对的是该条所属期号当时，不是今天；"
        "回答须改写为期号或绝对日期，禁止把相对时间原样当成当下。"
    )
    temporal = f"\n\n{temporal_block.strip()}\n" if (temporal_block or "").strip() else ""
    packed = pack_answer_contexts(contexts)
    # Answer 专用字符预算：显著低于抽取任务，避免把窗口塞满
    ans_budget = min(budget(reserve=28000), 9000)
    user = (
        f"问题：{question}\n\n{time_anchor}{temporal}\n{mode_hint}{hist_block}\n\n"
        f"可用记录（每条含 期号/章节/标题/内容）：\n"
        f"{json.dumps(packed, ensure_ascii=False)[:ans_budget]}\n\n"
        "请用中文简明回答：结论优先；每一句都能指回上面的记录；末尾列出「来源」。"
        "无法回答的部分说明缺哪类来源。不要展开无关分析。"
    )
    return system, user


def answer_question(
    question: str,
    contexts: list[dict],
    mode: str = "lexical",
    history: list[dict] | None = None,
    temporal_block: str = "",
    *,
    task: str = "ask",
) -> str:
    system, user = _qa_prompt(
        question, contexts, mode, history=history, temporal_block=temporal_block
    )
    return call(system, user, max_tokens=answer_max_tokens(), json_mode=False, task=task)


def answer_question_stream(
    question: str,
    contexts: list[dict],
    mode: str = "lexical",
    history: list[dict] | None = None,
    temporal_block: str = "",
):
    """流式问答：yield 文本片段。"""
    system, user = _qa_prompt(
        question, contexts, mode, history=history, temporal_block=temporal_block
    )
    yield from get_provider().stream(system, user, max_tokens=answer_max_tokens())

