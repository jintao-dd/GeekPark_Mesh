"""GeekPark Mesh · LLM 业务层

本文件**不得 import 任何厂商 SDK**。所有模型调用经 providers 适配层，
切换模型只改 .env 里的 MESH_LLM_PROVIDER，业务代码零改动。
"""
import json, re
from pathlib import Path
from .providers import get_provider, LLMError

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

def provider_info() -> dict:
    """后台"规则提示词"页显示当前模型配置，便于换模型后核对。"""
    try:
        return get_provider().describe()
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

def load_prompt(name: str) -> str:
    p = PROMPT_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""

def call(system: str, user: str, max_tokens: int = 4000, json_mode: bool = True):
    """模型调用。json_mode 下解析失败会自动再请求一次。"""
    last_err = None
    for attempt in range(2 if json_mode else 1):
        text = get_provider().complete(system, user, max_tokens=max_tokens)
        if not json_mode:
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
    """只改写命中禁用词的字符串字段，结构与其余文案不动。"""
    for _ in range(FIELD_REWRITE_ROUNDS):
        batch = _forbidden_field_paths(obj)
        if not batch:
            return obj
        for path, text, words in batch:
            ctx = _context_for_path(obj, path)
            new_text = _rewrite_text_field(text, ctx, words)
            if new_text and not forbidden_hits(new_text):
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
        out.append({"zone": zone, "level": level, "kind": it.get("kind", "fact"), "text": it.get("text", ""),
                    "entities": it.get("entities", []), "roles": it.get("roles", []), "signals": it.get("signals", []),
                    "owner_team": sanitize_owner_team(it.get("owner_team")),
                    "source_label": it.get("source_label", ""), "pointer": it.get("pointer", ""), "blocked": blocked, "team": it.get("team") or team})
    return out


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
) -> tuple[list[dict], dict | None]:
    """单个来源抽取：聚合包自动拆段并按段调用 extract_T*.md。

    返回 (items, split_meta)。split_meta 仅混合包有值，供 sources.meta 落库与 UI 展示。
    """
    from .aggregator import should_split, split_bundle_ex

    if stype == "T13" or should_split(stype=stype, team=team, channel=channel, title=title, text=text):
        split = split_bundle_ex(text, source_title=title or "")
        if not split.segments:
            msg = "；".join(split.warnings) if split.warnings else "正文为空或无可抽取段落"
            raise ValueError(f"无法抽取：{msg}")
        ch = (channel or "aggregator").strip() or "aggregator"
        out: list[dict] = []
        for seg in split.segments:
            seg_title = f"{title or '数据聚合'} · {seg.title}"[:200]
            items = extract_items(
                seg.stype, seg.team, seg_title, seg.text,
                period_start=period_start, period_end=period_end,
                period_label=period_label, channel=ch,
            )
            for it in items:
                it["item_stype"] = seg.stype
                out.append(it)
        return out, split.to_meta()
    items = extract_items(
        stype, team, title, text,
        period_start=period_start, period_end=period_end,
        period_label=period_label, channel=channel,
    )
    return items, None

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
def build_issue_draft(period: dict, approved_items: list[dict], first_names: list[str], external_items: list[dict], prev_issue_summary: str) -> dict:
    system = load_prompt("00_base_rules") + "\n\n" + load_prompt("issue_draft") + "\n\n" + load_prompt("91_output_issue")
    user = (f"期号：{period['slug']}；区间：{period['date_start']} 至 {period['date_end']}；显示：{period['period_label']}\n"
            f"归档比对得出的『本期首次进入记录的公司/人/话题』候选：{json.dumps(first_names, ensure_ascii=False)}\n"
            f"上一期摘要（避免重复、便于比较）：{prev_issue_summary[:3000]}\n\n"
            f"【有效内部条目】\n{json.dumps(approved_items, ensure_ascii=False)[:budget(40000)]}\n\n"
            f"【外部媒体条目（只用于生成'外部在热聊，我们还没碰'）】\n{json.dumps(external_items, ensure_ascii=False)[:20000]}\n\n"
            "请输出完整周报 JSON。全篇任何字段都不得出现全局禁用词；关键词墙 rows 也必须换说法，禁止照抄条目原话。")
    return call_json_compliant(system, user, max_tokens=16000)

# ---------- 4. AI 问答 ----------
def _qa_prompt(
    question: str,
    contexts: list[dict],
    mode: str = "lexical",
    history: list[dict] | None = None,
) -> tuple[str, str]:
    system = load_prompt("00_base_rules") + "\n\n" + load_prompt("qa")
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
            + json.dumps(history[-6:], ensure_ascii=False)[:4000]
        )
    user = (
        f"问题：{question}\n\n{mode_hint}{hist_block}\n\n"
        f"可用记录（每条含 期号/章节/标题/内容）：\n"
        f"{json.dumps(contexts, ensure_ascii=False)[:budget()]}\n\n"
        "请用中文回答，每一句都要能指回上面的记录；回答末尾列出'来源'。无法回答的部分要说明缺哪类来源。"
    )
    return system, user


def answer_question(
    question: str,
    contexts: list[dict],
    mode: str = "lexical",
    history: list[dict] | None = None,
) -> str:
    system, user = _qa_prompt(question, contexts, mode, history=history)
    return call(system, user, max_tokens=2000, json_mode=False)


def answer_question_stream(
    question: str,
    contexts: list[dict],
    mode: str = "lexical",
    history: list[dict] | None = None,
):
    """流式问答：yield 文本片段。"""
    system, user = _qa_prompt(question, contexts, mode, history=history)
    yield from get_provider().stream(system, user, max_tokens=2000)

