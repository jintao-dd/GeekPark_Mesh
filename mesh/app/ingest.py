"""GeekPark Mesh · 数据接入（文件解析 + RSS 抓取）"""
import re, json, csv, io, os, datetime, html as _html
from pathlib import Path

# 数据类型（与交接文档 §3 一致）
SOURCE_TYPES = {
    "T1": "编辑部沟通记录（多维表格导出 CSV/JSON/DOCX）",
    "T2": "编辑部选题表（多维表格导出）",
    "T3": "硅谷 BD 团队行程与建联（日程表 CSV / 日历 ICS / 建联记录）",
    "T4": "私域朋友圈·群聊（结构化条目 CSV/JSON/TXT）",
    "T5": "已发布内容（中文站/英文站 RSS 自动抓取；视频号脚本库 DOCX）",
    "T6": "各团队例会文档（妙记纪要 / 工作周报 / 会议转写 DOCX/TXT）",
    "T7": "外部媒体（RSS 自动抓取，仅用于'外部在热聊，我们还没碰'）",
    "T8": "主体名单（客户/意向、社群成员、投资在看 CSV）",
    "T9": "活动档案素材（复盘文档）",
    "T10": "Global Partnership 团队工作周报（DOCX）",
    "T11": "视频号周数据（后台导出 CSV）",
    "T12": "音频播客单期文档（DOCX/TXT）",
    "T13": "内容中心·数据聚合（混合多类型，系统按段分别抽取）",
}
AGG_STYPE = "T13"
AGG_TEAM = "内容中心·数据聚合"


def is_aggregation_source(*, stype: str = "", team: str = "", channel: str = "") -> bool:
    """仅内容中心混合包需要拆段/混挂确认。

    其他来源上传时选了哪个团队，就是该团队提供的内容，不因「长文只拆 1 段」拦预览。
    channel=aggregator 不能单独作依据（单团队纪要也可能被标成 aggregator）。
    """
    if (stype or "").strip() == AGG_STYPE:
        return True
    if (team or "").strip() == AGG_TEAM:
        return True
    return False


TEAMS = [
    "编辑部", "商业化团队", "硅谷 BD 团队", "Global Partnership 团队", "英文站",
    "品牌创意团队", "社群", "投资团队", "音频播客团队", "视频号团队",
    "CEO / 总裁办", "外部媒体", "内容中心·数据聚合", "其他",
]

# 编辑部在 UI 拆成三种材料（owner 仍归「编辑部」）
EDITORIAL_PICKS = {
    "编辑部 · 沟通记录": "T1",
    "编辑部 · 选题表": "T2",
    "编辑部 · 周例会": "T6",
}

# 上传页选项 → 默认 stype（含编辑部子类）
TEAM_DEFAULT_STYPE = {
    **EDITORIAL_PICKS,
    "商业化团队": "T6",
    "硅谷 BD 团队": "T3",
    "Global Partnership 团队": "T10",
    "英文站": "T5",
    "品牌创意团队": "T6",
    "社群": "T4",
    "投资团队": "T8",
    "音频播客团队": "T12",
    "视频号团队": "T11",
    "CEO / 总裁办": "T6",
    "外部媒体": "T7",
    "内容中心·数据聚合": "T13",
    "其他": "T6",
}

SOURCE_PICKS = list(EDITORIAL_PICKS.keys()) + [
    t for t in TEAMS if t not in ("编辑部", "内容中心·数据聚合", "其他")
] + ["内容中心·数据聚合", "其他"]

# stype → 团队（仅在上传推断不出团队时用）
STYPE_DEFAULT_TEAM = {
    "T1": "编辑部", "T2": "编辑部", "T3": "硅谷 BD 团队", "T4": "社群",
    "T5": "英文站", "T6": "编辑部", "T7": "外部媒体", "T8": "投资团队",
    "T9": "品牌创意团队", "T10": "Global Partnership 团队", "T11": "视频号团队",
    "T12": "音频播客团队", "T13": "内容中心·数据聚合",
}


def editorial_pick_for_stype(stype: str) -> str:
    return {
        "T1": "编辑部 · 沟通记录",
        "T2": "编辑部 · 选题表",
        "T6": "编辑部 · 周例会",
    }.get((stype or "").strip(), "编辑部 · 沟通记录")


def source_pick_for(team: str, stype: str = "") -> str:
    """归一上传页选项（sources.team 存此值）。"""
    t = (team or "").strip()
    if t in TEAM_DEFAULT_STYPE:
        return t
    if t == "编辑部" or (t.startswith("编辑部") and t not in TEAM_DEFAULT_STYPE):
        code = (stype or "").strip()
        if code not in EDITORIAL_PICKS.values():
            code = "T1"
        return editorial_pick_for_stype(code)
    c = canonical_team(t)
    if c in TEAM_DEFAULT_STYPE:
        return c
    return t or "其他"


def owner_team_for_pick(pick: str) -> str:
    """上传选项 → 条目 owner_team（要点卡 / 上线归属）。"""
    p = (pick or "").strip()
    if p.startswith("编辑部"):
        return "编辑部"
    return canonical_team(p) or p


def default_stype_for_team(team: str) -> str:
    return TEAM_DEFAULT_STYPE.get(source_pick_for(team), "T6")


def canonical_source_pick(name: str) -> str:
    return source_pick_for(name)


def canonical_team(name: str) -> str:
    """规范团队名（含旧称「播客」→「音频播客团队」）。"""
    from . import db
    n = (name or "").strip()
    if not n:
        return ""
    normed = db.normalize_team(n)
    if normed and normed in TEAMS:
        return normed
    return n


_AGG_BUNDLE_KEYS = (
    "数据聚合",
    "内容聚合",
    "内容中心",
    "内容中心·数据聚合",
    "聚合器",
)


def _is_aggregation_bundle(name: str, text: str = "", *, filename_only: bool = False) -> bool:
    """内容中心·数据聚合 等混合导出包（整份多来源，不能按正文目录行判单一 T 类型）。"""
    probe = name if filename_only else f"{name}\n{(text or '')[:1200]}"
    return any(k in probe for k in _AGG_BUNDLE_KEYS)


def _match_stype(name: str, text: str, *, filename_only: bool = False) -> str:
    """按关键词推断数据类型；filename_only 时只看文件名（避免聚合包目录行误触发 T3 等）。"""
    ext = Path(name).suffix.lower()
    head = f"{name}\n" if filename_only else f"{name}\n{(text or '')[:5000]}"
    head_l = head.lower()

    if ext == ".ics":
        return "T3"
    if ext == ".csv" and any(k in head for k in ("完播率", "平均观看时长", "视频号周数据", "视频号数据", "视频号后台")):
        return "T11"
    if ext == ".csv" and any(k in head for k in ("选题表", "选题池", "编辑部选题", "主题池", "刊发")):
        return "T2"

    type_rules = [
        ("T10", ("GP 工作进展", "GP工作进展", "GP 工作周报", "Global Partnership", "前沿社活跃度", "前沿社")),
        ("T11", ("视频号周数据", "视频号数据", "完播率", "视频号后台", "平均观看时长")),
        ("T12", ("音频播客", "播客第", "播客 ·", "podcast")),
        ("T3", ("行程", "建联", "硅谷 BD", "出差")),
        ("T2", ("选题表", "选题池", "编辑部选题")),
        ("T1", ("沟通记录", "一手对话", "攻坚讨论", "多维表格", "飞书妙记导出")),
        ("T4", ("朋友圈", "群聊", "私域", "社群聊天")),
        ("T5", ("视频号脚本", "脚本库", "已发布", "中文站", "英文站")),
        ("T8", ("主体名单", "意向客户", "在看名单", "客户名单")),
        ("T9", ("活动档案", "活动复盘", "复盘文档")),
        ("T7", ("外部媒体", "36氪", "虎嗅", "晚点", "财新")),
        ("T6", ("例会", "妙记", "周报", "会议纪要", "会议转写", "讨论", "工作进展")),
    ]
    for code, keys in type_rules:
        if any(k.lower() in head_l or k in head for k in keys):
            return code
    if ext in (".txt", ".md", ".docx"):
        return "T6"
    return "T1"


def _match_team(name: str, text: str, *, filename_only: bool = False) -> str:
    head = f"{name}\n" if filename_only else f"{name}\n{(text or '')[:5000]}"
    head_l = head.lower()
    team_rules = [
        ("硅谷 BD 团队", ("硅谷", "BD团队", "bd 团队")),
        ("Global Partnership 团队", ("Global Partnership", "全球合作")),
        ("英文站", ("英文站", "geekpark.media", "geekpark english", "about.geekpark", "极客公园英文站")),
        ("商业化团队", ("商业化", "广告销售")),
        ("品牌创意团队", ("品牌创意", "品牌部")),
        ("社群", ("社群", "私域运营")),
        ("投资团队", ("投资团队", "投资部")),
        ("音频播客团队", ("音频播客", "播客第", "播客 ·", "播客", "podcast")),
        ("视频号团队", ("视频号周数据", "视频号数据", "视频号后台", "完播率")),
        ("CEO / 总裁办", ("总裁办", "CEO", "总裁办")),
        ("编辑部", ("编辑部", "选题", "沟通记录", "攻坚", "一手")),
        ("外部媒体", ("外部媒体", "36氪", "虎嗅")),
    ]
    for tname, keys in team_rules:
        if any(k.lower() in head_l or k in head for k in keys):
            return tname
    return ""


def infer_source_meta(filename: str, text: str = "", default_team: str = "", *, filename_only: bool = False) -> dict:
    """推断数据类型、团队、通道。

    filename_only=True（文件上传）：只看文件名，不看正文，避免内容误触发。
    粘贴文本等无可靠文件名时仍可用正文辅助推断。
    """
    name = (filename or "").strip()
    head = name if filename_only else f"{name}\n{(text or '')[:5000]}"

    # 通道：明显聚合/外部源才标 aggregator
    channel = "aggregator" if any(k in head for k in _AGG_BUNDLE_KEYS + ("RSS", "外部媒体抓取")) else "manual"

    bundle = _is_aggregation_bundle(name, text, filename_only=filename_only)

    if bundle:
        stype = AGG_STYPE
        team = "内容中心·数据聚合"
    else:
        stype_guess = _match_stype(name, text, filename_only=filename_only)
        team_guess = _match_team(name, text, filename_only=filename_only)
        if not team_guess:
            team_guess = (default_team or "").strip()
        if not team_guess:
            team_guess = STYPE_DEFAULT_TEAM.get(stype_guess, "编辑部")
        team_guess = canonical_team(team_guess)
        if team_guess not in TEAMS:
            team_guess = "其他"
        if team_guess == "编辑部":
            stype = stype_guess if stype_guess in EDITORIAL_PICKS.values() else "T1"
            team = editorial_pick_for_stype(stype)
        else:
            team = source_pick_for(team_guess)
            stype = default_stype_for_team(team)

    title = Path(name).stem if name else ""
    return {"stype": stype, "team": team, "channel": channel, "title": title}


def _decode_bytes(data: bytes) -> str:
    """UTF-8 优先；常见中文 Windows 导出回退 gb18030/gbk。"""
    if not data:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_upload(filename: str, data: bytes) -> tuple[str, dict]:
    """把上传文件转成纯文本；返回 (text, meta)"""
    ext = Path(filename).suffix.lower()
    meta = {"ext": ext, "size": len(data)}
    if ext == ".docx":
        try:
            import docx
            d = docx.Document(io.BytesIO(data))
            parts = []
            for p in d.paragraphs:
                if p.text.strip(): parts.append(p.text)
            for t in d.tables:
                for row in t.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells): parts.append(" | ".join(cells))
            return "\n".join(parts), meta
        except Exception as e:
            meta["error"] = f"docx 解析失败：{e}"
            return "", meta
    if ext in (".txt", ".md", ".log"):
        return _decode_bytes(data), meta
    if ext == ".csv":
        text = _decode_bytes(data)
        rows = list(csv.reader(io.StringIO(text)))
        meta["rows"] = len(rows)
        return "\n".join(" | ".join(r) for r in rows), meta
    if ext == ".json":
        try:
            obj = json.loads(_decode_bytes(data))
            return json.dumps(obj, ensure_ascii=False, indent=1), meta
        except Exception as e:
            meta["error"] = f"json 解析失败：{e}"; return _decode_bytes(data), meta
    if ext == ".ics":
        return parse_ics(_decode_bytes(data)), meta
    if ext in (".html", ".htm"):
        return html_to_text(_decode_bytes(data)), meta
    meta["error"] = "不支持的格式（支持 docx/txt/md/csv/json/ics/html）"
    return "", meta

def parse_ics(text: str) -> str:
    """极简 ICS 解析：SUMMARY / DTSTART / DTEND / LOCATION / DESCRIPTION"""
    events, cur = [], None
    for raw in text.replace("\r\n ", "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line == "BEGIN:VEVENT": cur = {}
        elif line == "END:VEVENT" and cur is not None: events.append(cur); cur = None
        elif cur is not None and ":" in line:
            k, v = line.split(":", 1); k = k.split(";")[0]
            if k in ("SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION", "URL"): cur[k] = v
    out = []
    for e in events:
        out.append(f"事件：{e.get('SUMMARY','')} ｜ 开始：{e.get('DTSTART','')} ｜ 结束：{e.get('DTEND','')} ｜ 地点：{e.get('LOCATION','')} ｜ 说明：{e.get('DESCRIPTION','')[:300]}")
    return "\n".join(out) if out else "（ICS 中未解析到事件）"

def html_to_text(h: str) -> str:
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = _html.unescape(h)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", h)).strip()

# ---------- RSS ----------
DEFAULT_FEEDS = {
    "geekpark_cn": {"url": "https://www.geekpark.net/rss", "team": "编辑部", "stype": "T5", "label": "极客公园中文站"},
    "geekpark_en": {"url": "https://about.geekpark.net/rss.xml", "team": "英文站", "stype": "T5", "label": "极客公园英文站"},
}
EXTERNAL_FEEDS_DEFAULT = [
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://venturebeat.com/feed/",
    "https://www.digitaltrends.com/feed/",
    "https://www.technologyreview.com/feed/",
]
NOISE_PATTERNS = re.compile(r"(deal|deals|best .* of|how to|buying guide|coupon|sale|discount|giveaway|review roundup|world cup|nfl|nba)", re.I)

def fetch_feed(url: str, max_items: int = 30, fresh_days: int = 7, allow_stale: bool = False):
    """返回 [(title, link, author, published, summary, stale, noise)]"""
    import feedparser
    d = feedparser.parse(url)
    out, now = [], datetime.datetime.now(datetime.timezone.utc)
    for e in d.entries[:max_items]:
        pub = None
        for k in ("published_parsed", "updated_parsed"):
            if getattr(e, k, None):
                pub = datetime.datetime(*getattr(e, k)[:6], tzinfo=datetime.timezone.utc); break
        stale = bool(pub and (now - pub).days > fresh_days)
        title = getattr(e, "title", "")
        noise = bool(NOISE_PATTERNS.search(title or ""))
        summary = html_to_text(getattr(e, "summary", "") or "")[:600]
        author = getattr(e, "author", "") or ""
        if stale and not allow_stale: continue
        out.append({"title": title, "link": getattr(e, "link", ""), "author": author,
                    "published": pub.strftime("%Y-%m-%d") if pub else "", "summary": summary, "stale": stale, "noise": noise})
    return out

def feed_to_text(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        flag = "【过期】" if e["stale"] else ""
        flag += "【噪音】" if e["noise"] else ""
        lines.append(f"{flag}《{e['title']}》 ｜ {e['published']} ｜ {e['author']} ｜ {e['link']}\n  {e['summary']}")
    return "\n".join(lines)
