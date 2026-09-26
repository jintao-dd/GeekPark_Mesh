"""内容中心·数据聚合包：按章节拆段，各段走对应 extract_T*.md。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import ingest
from .ingest import AGG_STYPE

AGG_TEAM = "内容中心·数据聚合"
_MIN_SEGMENT = 80

# 无 ### 的旧式聚合壳：仅认「emoji + 已知章节名」，避免正文里 🎯 话题行被二次切开
_MAJOR_KNOWN = re.compile(
    r"^[\U0001F300-\U0001FAFF📆📊🏢🗂📓🌐📰📱🎙🔬🚀🎓💻📡🏟⚡]\s*"
    r"(?:"
    r"内容统计|内部飞书(?:内容|文件夹)?|外部信息源?|"
    r"飞书多维表格|会议日程(?:\s*\(ICS\))?|Notion CRM|"
    r"视频号数据|编辑部\s*·\s*(?:选题|沟通记录)|"
    r"TechCrunch|Wired|The Verge|Stratechery|Platformer|"
    r"Lex Fridman|How I Built This|Ars Technica|VentureBeat|"
    r"MIT Technology Review|ZDNet|TechRadar|Digital Trends|极客公园|GeekPark English"
    r")\b",
    re.I,
)
# 飞书多维表格内：视频号数据： / 编辑部数据：
_DATA_SUB = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9 /·&]{2,40}数据：\s*$")
# 外部源块：TechCrunch-综合 - 综合
_FEED_SUB = re.compile(
    r"^(TechCrunch|Wired|The Verge|Ars Technica|Engadget|VentureBeat|CNET|ZDNet|"
    r"TechRadar|Digital Trends|MIT Technology Review|Platformer|Stratechery|Acquired)-"
)
# 飞书文件夹内各团队文档标题行（勿单独匹配「沟通记录」二字，否则会切开多维表沟通段正文）
_INTERNAL = re.compile(
    r"^(?:"
    r"GP[\s·]?工作(?:进展|周报)?"
    r"|Global Partnership"
    r"|前沿社"
    r"|攻坚讨论"
    r"|飞书妙记"
    r"|(?:[\u4e00-\u9fffA-Za-z0-9 /·&]{2,20})(?:例会|周报|会议纪要|工作进展|妙记转写)"
    r")(?:\s|$|·|：|:|\.)",
    re.I,
)

_SKIP_PREFIXES = (
    "📊 内容统计",
    "🏢 内部飞书内容",
    "🗂 内部飞书文件夹",
    "本节内容来自指定飞书文件夹",
    "📊 飞书多维表格",
)

_AI_SECTION = re.compile(r"^##\s*🤖")
_RAW_DATA_SECTION = re.compile(r"^##\s*(🏢|🌐)")

# 与导出 MD「内容统计」目录一致的正文章节（### 级；单条记录如「### 英伟达播客」不算）
_EXTERNAL_FEED_NAMES = (
    "TechCrunch", "Wired", "The Verge", "Stratechery", "Platformer",
    "Lex Fridman", "How I Built This", "Ars Technica", "VentureBeat",
    "MIT Technology Review", "ZDNet", "TechRadar", "Digital Trends",
    "极客公园", "GeekPark English",
)

_TOC_BULLET = re.compile(
    r"^\s*-\s+\*\*(?P<name>[^*]+)\*\*:\s*(?P<count>\d+)\s*(?:条记录|条|篇)?",
    re.M,
)
_TOC_SUB = re.compile(
    r"^\s*-\s+(?P<name>编辑部\s*·\s*(?:选题|沟通记录)|视频号数据):\s*(?P<count>\d+)\s*条",
    re.M,
)


def parse_content_stats_toc(text: str) -> list[dict]:
    """解析 📊 内容统计 下的目录项（用于校验 / 元数据 / 补边界）。"""
    m = re.search(r"^##?\s*📊\s*内容统计\s*$", text, re.M)
    if not m:
        m = re.search(r"^📊\s*内容统计\s*$", text, re.M)
    if not m:
        return []
    rest = text[m.end():]
    end = re.search(r"^##\s+", rest, re.M)
    block = rest[: end.start()] if end else rest[:2500]
    out: list[dict] = []
    for line in block.splitlines():
        sub = _TOC_SUB.match(line)
        if sub:
            out.append({"name": re.sub(r"\s+", " ", sub.group("name")).strip(), "count": int(sub.group("count"))})
            continue
        bul = _TOC_BULLET.match(line)
        if bul:
            out.append({"name": bul.group("name").strip(), "count": int(bul.group("count"))})
    return out


def _strip_heading_noise(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^#+\s*", "", s)
    s = re.sub(r"^[\U0001F300-\U0001FAFF📆📊🏢🗂📓🌐📰📱🎙🔬🚀🎓💻📡]+\s*", "", s)
    return s.strip()


def _raw_data_start_line(lines: list[str]) -> int:
    """跳过 🤖 AI智能分析，从 🏢/🌐 原始数据区开始拆。"""
    for i, line in enumerate(lines):
        if _RAW_DATA_SECTION.match(line.strip()):
            return i
    for i, line in enumerate(lines):
        if _AI_SECTION.match(line.strip()):
            for j in range(i + 1, len(lines)):
                if _RAW_DATA_SECTION.match(lines[j].strip()):
                    return j
            break
    return 0


def _is_md_section_boundary(line: str) -> bool:
    """正文切段边界：### / #### 级；##### 单条记录不算。

    「飞书多维表格」只是壳，真正切点是其下 #### 选题 / 视频号 / 沟通记录。
    """
    s = line.strip()
    if s.startswith("#####"):
        return False
    if not (s.startswith("### ") or s.startswith("#### ")):
        return False
    body = _strip_heading_noise(s)
    # 壳：不单独成段，留给子 #### 切开
    if body.startswith("飞书多维表格") or body == "飞书多维表格":
        return False
    if re.match(r"会议日程", body) or "会议日程" in body and "ICS" in body:
        return True
    if re.match(r"编辑部\s*·\s*(选题|沟通记录)", body):
        return True
    if body == "视频号数据" or ("视频号" in body and "数据" in body and len(body) <= 16):
        return True
    if "Notion CRM" in body:
        return True
    if body.startswith("🗂"):
        return False
    if "行业观察" in body or "匹配建联" in body:
        return False
    if body.startswith("记录 "):
        return False
    for name in _EXTERNAL_FEED_NAMES:
        if body == name or (name in body and len(body) <= len(name) + 12):
            return True
    return False


@dataclass
class Segment:
    title: str
    text: str
    stype: str
    team: str
    owner_hint: str = ""


@dataclass
class SplitResult:
    segments: list[Segment]
    mode: str = "multi"  # multi | single | fallback
    warnings: list[str] = field(default_factory=list)
    boundaries: int = 0
    skipped: int = 0
    toc_count: int = 0

    def to_meta(self) -> dict:
        conf = split_confidence(self)
        return {
            "segments": len(self.segments),
            "mode": self.mode,
            "boundaries": self.boundaries,
            "skipped": self.skipped,
            "toc_count": self.toc_count,
            "confidence": conf,
            "warnings": self.warnings,
            "types": _stype_counts(self.segments),
        }


def split_confidence(split: "SplitResult | dict") -> str:
    """拆段置信度：high / medium / low。

    high → 可上传预拆、一般不需人审
    medium → 不预拆炸子源，抽取时再拆；通常不拦人
    low → 需人工确认（仅聚合源）
    """
    if isinstance(split, dict):
        mode = (split.get("mode") or "").strip()
        n = int(split.get("segments") or 0)
        toc = int(split.get("toc_count") or 0)
        boundaries = int(split.get("boundaries") or 0)
        warnings = split.get("warnings") or []
    else:
        mode = (split.mode or "").strip()
        n = len(split.segments or [])
        toc = int(split.toc_count or 0)
        boundaries = int(split.boundaries or 0)
        warnings = split.warnings or []

    if mode in ("fallback", "empty"):
        return "low"
    if mode == "single":
        if boundaries > 1 and n <= 1:
            return "low"
        if warnings and n <= 1:
            return "low"
        return "low"
    # multi
    if n >= 4 and (toc <= 0 or n * 2 >= toc):
        return "high"
    if n >= 3 and (toc <= 0 or n * 3 >= toc):
        return "medium"
    if toc >= 6 and n > 0 and n * 3 < toc:
        return "low"
    if n >= 2:
        return "medium"
    return "low"


def should_pre_explode(split: "SplitResult") -> bool:
    """仅高置信 multi 才在上传时炸成子来源（减少错切绕过确认）。"""
    return (
        split.mode == "multi"
        and len(split.segments) > 1
        and split_confidence(split) == "high"
    )



def should_split(*, stype: str = "", team: str = "", channel: str = "", title: str = "", text: str = "") -> bool:
    return is_mixed_source(stype=stype, team=team, channel=channel, title=title, text=text)


INVALID_OWNER_TEAMS = frozenset({AGG_TEAM, "其他"})


def is_mixed_source(*, stype: str = "", team: str = "", channel: str = "", title: str = "", text: str = "") -> bool:
    if (stype or "").strip() == AGG_STYPE:
        return True
    if (team or "").strip() == AGG_TEAM:
        return True
    if (channel or "").strip() == "aggregator" and ingest._is_aggregation_bundle(title, text):
        return True
    return ingest._is_aggregation_bundle(title, text)


def sanitize_owner_team(name: str | None) -> str | None:
    n = ingest.canonical_team((name or "").strip())
    if not n or n in INVALID_OWNER_TEAMS or n not in ingest.TEAMS:
        return None
    return n


def resolve_item_owner(it: dict, *, source_team: str, segment_team: str | None = None) -> str | None:
    """入库前解析条目 owner_team（见 app.attribution）。"""
    from .attribution import resolve_item_owner as _resolve

    hint = sanitize_owner_team(segment_team) or sanitize_owner_team(it.get("_segment_team"))
    return _resolve(it, source_team=source_team, segment_team=hint)


def merge_source_meta(existing: str | dict | None, split_meta: dict | None) -> str:
    base: dict = {}
    if existing:
        try:
            base = json.loads(existing) if isinstance(existing, str) else dict(existing)
        except (json.JSONDecodeError, TypeError):
            base = {}
    if split_meta:
        base["split"] = split_meta
    return json.dumps(base, ensure_ascii=False)


def split_hint(meta: str | dict | None) -> str:
    """给人看的拆段摘要，用于控制台 / 来源页。"""
    if not meta:
        return ""
    try:
        m = json.loads(meta) if isinstance(meta, str) else meta
    except (json.JSONDecodeError, TypeError):
        return ""
    sp = (m or {}).get("split") or {}
    if not sp:
        return ""
    n = sp.get("segments", 0)
    mode = sp.get("mode", "")
    if mode == "pre_split":
        idx = sp.get("segment_index")
        n = sp.get("segments", 0)
        if idx is not None and n:
            return f"上传已预拆 {idx + 1}/{n}"
        return "上传已预拆"
    if mode == "multi" and n > 1:
        types = sp.get("types") or {}
        brief = "、".join(f"{k}×{v}" for k, v in sorted(types.items()))
        return f"已拆 {n} 段（{brief}）"
    if mode == "single":
        return "整包 1 段（未识别多章节边界）"
    if mode == "fallback":
        return "降级整段抽取（格式未识别，请核对）"
    if n == 1:
        return "整包 1 段"
    return f"已拆 {n} 段"


def sources_from_split(
    split: SplitResult,
    *,
    parent_title: str,
    parent_filename: str,
    raw_path: str = "",
    base_meta: dict | None = None,
    upload_team: str = "",
) -> list[dict]:
    """把拆段结果物化为多条 sources 插入参数（上传时预拆，降低错归属）。

    若 upload_team 为非占位人工选项，各段 sources.team 沿用用户选择（不被段推断覆盖）；
    段推断团队仅写入 meta.split.segment_inferred_team 供审计。
    """
    from .attribution import is_placeholder_pick

    base = dict(base_meta or {})
    upload_pick = ingest.source_pick_for(upload_team) if upload_team else ""
    manual_upload = upload_pick and not is_placeholder_pick(upload_pick)
    out: list[dict] = []
    for i, seg in enumerate(split.segments):
        seg_inferred = sanitize_owner_team(seg.owner_hint or seg.team) or seg.team
        split_meta = {
            "mode": "pre_split",
            "parent_filename": parent_filename,
            "parent_title": parent_title,
            "segment_index": i,
            "segments": len(split.segments),
            "boundaries": split.boundaries,
            "toc_count": split.toc_count,
            "confidence": split_confidence(split),
            "warnings": split.warnings,
        }
        if manual_upload:
            split_meta["segment_inferred_team"] = seg_inferred
            split_meta["upload_team_override"] = upload_pick
        meta = {**base, "split": split_meta}
        # 展示名只用段标题（去掉 #/emoji），不拼父文件名，避免「内容聚合报告·…」盖住裁剪名
        display = _strip_heading_noise(seg.title) or (seg.title or "").strip() or f"段落{i + 1}"
        title = display[:200]
        team_for_source = upload_pick if manual_upload else seg_inferred
        out.append({
            "stype": seg.stype,
            "team": team_for_source,
            "title": title,
            "filename": parent_filename,
            "raw_path": raw_path,
            "text": seg.text,
            "meta": meta,
            "channel": "aggregator" if split.mode in ("multi", "fallback") else "manual",
        })
    return out


def _stype_counts(segments: list[Segment]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in segments:
        out[s.stype] = out.get(s.stype, 0) + 1
    return out


def _is_skip_title(title: str) -> bool:
    return any(title.startswith(p) for p in _SKIP_PREFIXES)


def _is_boundary(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _is_md_section_boundary(s):
        return True
    # 旧式无 # 标题的聚合包：仅已知章节名；正文 emoji 话题行（如 🎯 产品定义…）不算切点
    if _MAJOR_KNOWN.match(s):
        return True
    if _DATA_SUB.match(s):
        return True
    if _FEED_SUB.match(s):
        return True
    if s in ("📰 TechCrunch", "The Verge", "Ars Technica", "MIT Technology Review"):
        return True
    if _is_internal_boundary(s):
        return True
    return False


def _is_internal_boundary(line: str) -> bool:
    s = line.strip()
    if len(s) < 4 or len(s) > 100:
        return False
    if _INTERNAL.match(s):
        return True
    if s.endswith(".docx") and " " not in s[: max(0, len(s) - 6)]:
        return True
    return False


def _classify(title: str, body: str) -> tuple[str, str]:
    h = f"{title}\n{body[:6000]}"
    t = title.strip()
    if re.search(r"编辑部\s*·\s*选题", t):
        return "T2", "编辑部 · 选题表"
    if re.search(r"编辑部\s*·\s*沟通记录", t):
        return "T1", "编辑部 · 沟通记录"
    if "视频号数据" in title or ("完播率" in h and "视频标题" in h):
        return "T11", "视频号团队"
    if "会议日程" in title or "ICS" in title or "grip.events" in h[:800]:
        return "T3", "硅谷 BD 团队"
    if "Notion CRM" in title or "Interactions / Takes" in h or "People / Companies" in h:
        return "T3", "硅谷 BD 团队"
    # 自家站：
    # - GeekPark English → 英文站（有独立团队）
    # - 极客公园中文站 RSS → 外部媒体（公开发布文章，勿进「编辑部·沟通记录」；标题可区分自家 vs 外媒）
    if re.search(r"GeekPark\s*English|极客公园英文|about\.geekpark", title + "\n" + h[:800], re.I):
        return "T5", "英文站"
    if "极客公园" in title:
        return "T7", "外部媒体"
    if "外部信息" in title or _FEED_SUB.match(title.strip()):
        return "T7", "外部媒体"
    # 与拆段白名单一致：外媒 feed → 外部媒体（英文站已在上方处理）
    _OWN_EN = {"GeekPark English"}
    for name in _EXTERNAL_FEED_NAMES:
        if name in _OWN_EN:
            continue
        body_title = _strip_heading_noise(t) or t
        if body_title == name or name in title:
            return "T7", "外部媒体"
    if any(
        k in title
        for k in (
            "TechCrunch", "Wired", "The Verge", "Engadget", "VentureBeat",
            "Ars Technica", "CNET", "ZDNet", "TechRadar", "Digital Trends",
            "MIT Technology", "Platformer", "Stratechery", "Acquired",
            "Lex Fridman", "How I Built This",
        )
    ):
        return "T7", "外部媒体"
    # RSS 条目常见字段（聚合导出可能缺「链接」）
    if ("作者:" in h or "作者：" in h) and ("发布时间:" in h or "发布时间：" in h):
        return "T7", "外部媒体"
    if any(k in title for k in ("GP 工作", "GP工作", "Global Partnership", "前沿社")):
        return "T10", "Global Partnership 团队"
    if any(k in title for k in ("沟通记录", "攻坚讨论", "多维表格", "飞书妙记")):
        return "T1", "编辑部"
    st = ingest._match_stype(title, body)
    tm = ingest._match_team(title, body)
    if not tm:
        tm = {
            "T1": "编辑部", "T2": "编辑部", "T3": "硅谷 BD 团队", "T4": "社群",
            "T5": "编辑部", "T6": "编辑部", "T7": "外部媒体", "T8": "商业化团队",
            "T9": "品牌创意团队", "T10": "Global Partnership 团队", "T11": "视频号团队",
            "T12": "音频播客团队",
        }.get(st, "编辑部")
    return st, tm


def _chunk_to_segment(chunk_lines: list[str], *, owner_hint: str = "") -> Segment | None:
    from .owner_guard import owner_hint_for_lines

    while chunk_lines and not chunk_lines[0].strip():
        chunk_lines.pop(0)
    while chunk_lines and not chunk_lines[-1].strip():
        chunk_lines.pop()
    chunk = "\n".join(chunk_lines).strip()
    if len(chunk) < _MIN_SEGMENT:
        return None
    title = chunk_lines[0].strip()[:120] if chunk_lines else "未命名段落"
    stype, team = _classify(title, chunk)
    hint = owner_hint_for_lines(chunk_lines, fallback=owner_hint or team)
    return Segment(title=title, text=chunk, stype=stype, team=team, owner_hint=hint or team)


def _split_lines(lines: list[str], start: int) -> tuple[list[tuple[int, int]], int]:
    bounds: list[int] = []
    for i in range(start, len(lines)):
        if _is_boundary(lines[i]):
            bounds.append(i)
    boundary_count = len(bounds)
    if not bounds:
        bounds = [start]
    bounds.append(len(lines))
    pairs = list(zip(bounds, bounds[1:]))
    return pairs, boundary_count


def _segments_from_range(
    lines: list[str], a: int, b: int, *, skipped: list[int], section_at: list[str | None],
) -> list[Segment]:
    chunk_lines = lines[a:b]
    if not chunk_lines:
        return []
    hint = section_at[a] if a < len(section_at) else ""
    title = chunk_lines[0].strip()[:120] if chunk_lines[0].strip() else ""
    if _is_skip_title(title):
        inner_bounds = [i for i in range(1, len(chunk_lines)) if _is_boundary(chunk_lines[i])]
        if inner_bounds:
            skipped.append(1)
            segs: list[Segment] = []
            inner_bounds.append(len(chunk_lines))
            for x, y in zip(inner_bounds, inner_bounds[1:]):
                local_hint = hint or (section_at[a + x] if a + x < len(section_at) else "")
                seg = _chunk_to_segment(chunk_lines[x:y], owner_hint=local_hint or "")
                if seg:
                    segs.append(seg)
            return segs
        if len("\n".join(chunk_lines).strip()) >= _MIN_SEGMENT:
            skipped.append(1)
        return []
    seg = _chunk_to_segment(chunk_lines, owner_hint=hint or "")
    return [seg] if seg else []


def _build_section_map(lines: list[str]) -> list[str]:
    from .owner_guard import parse_section_team

    out: list[str] = []
    cur = ""
    for line in lines:
        t = parse_section_team(line)
        if t:
            cur = t
        out.append(cur)
    return out


def _fallback_segment(source_title: str, text: str) -> Segment:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    title = source_title.strip() or (lines[0][:120] if lines else "未命名段落")
    st = ingest._match_stype(source_title or title, text)
    if st == AGG_STYPE:
        st = ingest._match_stype(title, text)
    tm = ingest._match_team(source_title or title, text) or ingest._match_team(title, text)
    if not tm:
        tm = {
            "T1": "编辑部", "T2": "编辑部", "T3": "硅谷 BD 团队", "T4": "社群",
            "T5": "编辑部", "T6": "编辑部", "T7": "外部媒体", "T8": "商业化团队",
            "T9": "品牌创意团队", "T10": "Global Partnership 团队", "T11": "视频号团队",
            "T12": "音频播客团队",
        }.get(st, "编辑部")
    return Segment(title=title[:120], text=text.strip(), stype=st, team=tm, owner_hint=tm)


def _toc_name_boundary_indexes(lines: list[str], start: int, toc: list[dict]) -> list[int]:
    """用内容统计目录名在正文中补边界（欠拆时自动加刀）。"""
    names = []
    for item in toc or []:
        name = re.sub(r"\s+", " ", (item.get("name") or "").strip())
        # 去掉「(ICS)」等后缀便于匹配
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
        if name and name not in ("飞书多维表格",) and len(name) >= 2:
            names.append(name)
    if not names:
        return []
    hits: list[int] = []
    for i in range(start, len(lines)):
        plain = _strip_heading_noise(lines[i])
        if not plain or len(plain) > 80:
            continue
        for name in names:
            if plain == name or plain.startswith(name) or name in plain:
                # 避免正文长句误命中：标题行要短且名字占比高
                if len(plain) <= len(name) + 20:
                    hits.append(i)
                    break
    return hits


def _collect_segments(
    lines: list[str], start: int, extra_bounds: list[int] | None = None,
) -> tuple[list[Segment], int, int]:
    """返回 (segments, boundary_count, skipped_n)。"""
    pairs, boundary_count = _split_lines(lines, start)
    if extra_bounds:
        bounds = sorted(set([pairs[0][0]] + [a for a, _ in pairs] + list(extra_bounds) + [len(lines)]))
        # rebuild pairs from merged bounds that fall in range
        bounds = [b for b in bounds if start <= b <= len(lines)]
        if bounds and bounds[0] > start:
            bounds.insert(0, start)
        if not bounds or bounds[-1] != len(lines):
            bounds.append(len(lines))
        bounds = sorted(set(bounds))
        pairs = list(zip(bounds, bounds[1:]))
        boundary_count = max(boundary_count, len(bounds) - 1)

    section_at = _build_section_map(lines)
    skipped: list[int] = []
    segments: list[Segment] = []
    for a, b in pairs:
        segments.extend(_segments_from_range(lines, a, b, skipped=skipped, section_at=section_at))
    return segments, boundary_count, sum(skipped)


def _merge_adjacent_same_owner(segments: list[Segment]) -> list[Segment]:
    """合并相邻且同 stype+team 的短碎片，减少噪声段。"""
    if len(segments) < 2:
        return segments
    out: list[Segment] = []
    for seg in segments:
        if (
            out
            and out[-1].stype == seg.stype
            and (out[-1].owner_hint or out[-1].team) == (seg.owner_hint or seg.team)
            and len(seg.text) < 400
            and len(out[-1].text) < 1200
        ):
            prev = out[-1]
            out[-1] = Segment(
                title=prev.title,
                text=(prev.text + "\n\n" + seg.text).strip(),
                stype=prev.stype,
                team=prev.team,
                owner_hint=prev.owner_hint or prev.team,
            )
        else:
            out.append(seg)
    return out


def split_bundle_ex(text: str, *, source_title: str = "") -> SplitResult:
    """把聚合包正文切成多段；保证有正文时至少返回 1 段，并附带可追溯的拆段信息。"""
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return SplitResult(segments=[], mode="empty", warnings=["正文为空"])

    lines = raw.split("\n")
    start = _raw_data_start_line(lines)
    for i in range(start, len(lines)):
        if _is_boundary(lines[i]):
            start = i
            break

    toc = parse_content_stats_toc(raw)
    toc_n = len(toc) if toc else 0
    segments, boundary_count, skipped_n = _collect_segments(lines, start)

    # 欠拆：用目录名补边界再拆一次
    if toc_n >= 3 and (not segments or len(segments) * 2 < toc_n):
        extra = _toc_name_boundary_indexes(lines, start, toc)
        if extra:
            seg2, bc2, sk2 = _collect_segments(lines, start, extra_bounds=extra)
            if len(seg2) > len(segments):
                segments, boundary_count, skipped_n = seg2, bc2, sk2

    segments = _merge_adjacent_same_owner(segments)
    warnings: list[str] = []
    if toc and segments:
        warnings.append(f"内容统计目录 {len(toc)} 项；正文拆出 {len(segments)} 段")
        if toc_n >= 6 and len(segments) * 3 < toc_n:
            warnings.append(
                f"目录项远多于正文段落（{toc_n} vs {len(segments)}），可能拆段不足"
            )

    if not segments and len(raw) >= _MIN_SEGMENT:
        seg = _fallback_segment(source_title, raw)
        warnings.append(
            "未能识别章节边界（可能导出格式变更或误标 T13），已按正文推断类型整段抽取；"
            "建议核对条目归属，或拆成单部门文件后重传。"
        )
        return SplitResult(
            segments=[seg],
            mode="fallback",
            warnings=warnings,
            boundaries=boundary_count,
            skipped=skipped_n,
            toc_count=toc_n,
        )

    if not segments:
        return SplitResult(
            segments=[],
            mode="empty",
            warnings=["正文过短或无可抽取段落"],
            boundaries=boundary_count,
            skipped=skipped_n,
            toc_count=toc_n,
        )

    mode = "multi" if len(segments) > 1 else "single"
    if mode == "single" and boundary_count <= 1 and len(raw) > 8000:
        warnings.append(
            "长文仅拆出 1 段：可能缺少 emoji / 「xxx数据：」等章节标题；"
            "条目仍可先审，建议核对 T 类型与归属。"
        )
    elif mode == "single" and boundary_count > 1:
        warnings.append("识别到多个边界但有效段落仅 1 段，可能有壳段落被跳过。")

    return SplitResult(
        segments=segments,
        mode=mode,
        warnings=warnings,
        boundaries=boundary_count,
        skipped=skipped_n,
        toc_count=toc_n,
    )


def split_bundle(text: str) -> list[Segment]:
    """兼容旧调用：只返回段落列表。"""
    return split_bundle_ex(text).segments
