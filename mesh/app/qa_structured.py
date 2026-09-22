"""跨部门交叉问答：意图分流 + 事实表 SQL（差集/交集/海外缺口/按团队聚合）。"""
from __future__ import annotations
import os, re, datetime
from . import db, ingest

DEFAULT_WINDOW_DAYS = int(
    os.environ.get("MESH_QA_WINDOW_DAYS")
    or os.environ.get("MESH_QA_LEXICAL_WINDOW_DAYS")
    or "90"
)

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
RESULT_LIMIT = int(os.environ.get("MESH_QA_RESULT_LIMIT", "30") or "30")

OVERSEAS_TEAMS_DEFAULT = ["硅谷 BD 团队", "Global Partnership 团队", "英文站"]
DOMESTIC_TEAMS_DEFAULT = [
    "编辑部", "商业化团队", "品牌创意团队", "社群", "投资团队",
    "音频播客团队", "视频号团队", "CEO / 总裁办",
]
HARDWARE_HINTS = (
    "硬件", "助听器", "打印机", "一体机", "ODM", "传感器", "芯片", "机器人", "耳机",
    "穿戴", "智能硬件", "消费电子", "设备", "终端",
)
_BAD_BY_TEAM_TOPICS = {
    "什么", "哪些", "哪个", "怎么", "如何", "多少", "一下", "东西", "内容", "情况",
    "消息", "分别", "知道", "了解", "团队", "各团队",
}
_ASK_ALIAS_BLOCK = {"编辑", "投资", "品牌", "FP", "GP"}


def _split_env_teams(key: str, default: list[str]) -> list[str]:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return list(default)
    out = []
    for part in re.split(r"[,，;；]", raw):
        n = db.normalize_team(part.strip()) or part.strip()
        if n and n not in out:
            out.append(n)
    return out or list(default)


def overseas_teams() -> list[str]:
    return _split_env_teams("MESH_OVERSEAS_TEAMS", OVERSEAS_TEAMS_DEFAULT)


def domestic_teams() -> list[str]:
    return _split_env_teams("MESH_DOMESTIC_TEAMS", DOMESTIC_TEAMS_DEFAULT)


def parse_window(q: str) -> tuple[str | None, str | None, int]:
    """返回 (date_from, date_to, window_days)。
    date_to=None 表示上界不限；闭区间用于上周/上月/去年。
    「全部历史」等 → (None, None, 0)。"""
    q = q or ""
    if re.search(r"全部历史|所有期|历史以来|不限时间|有史以来|不限日期|全部时期|所有历史|历年", q):
        return None, None, 0
    today = datetime.date.today()

    def _span(a: datetime.date, b: datetime.date) -> int:
        return max(1, (b - a).days)

    m = re.search(r"过去\s*(\d+)\s*个?月", q)
    if m:
        days = int(m.group(1)) * 30
        return (today - datetime.timedelta(days=days)).isoformat(), None, days
    m = re.search(r"(?:近|过去|最近)\s*(\d+)\s*个?月", q)
    if m:
        days = int(m.group(1)) * 30
        return (today - datetime.timedelta(days=days)).isoformat(), None, days
    m = re.search(r"(?:近|过去|最近)\s*(\d+)\s*(?:个)?年", q)
    if m:
        days = int(m.group(1)) * 365
        return (today - datetime.timedelta(days=days)).isoformat(), None, days
    m = re.search(r"(?:近|过去|最近)\s*([一二三四五六七八九十两])\s*年|([一二三四五六七八九十两])年内", q)
    if m:
        n = _CN_NUM.get(m.group(1) or m.group(2), 1)
        days = n * 365
        return (today - datetime.timedelta(days=days)).isoformat(), None, days
    m = re.search(r"(?:近|过去)\s*(\d+)\s*天", q)
    if m:
        days = int(m.group(1))
        return (today - datetime.timedelta(days=days)).isoformat(), None, days
    m = re.search(r"(?:近|最近|过去)\s*(\d+)\s*期", q)
    if m:
        days = int(m.group(1)) * 7
        return (today - datetime.timedelta(days=days)).isoformat(), None, days
    if re.search(r"本周|这周", q):
        start = today - datetime.timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat(), _span(start, today)
    if re.search(r"上周", q):
        this_mon = today - datetime.timedelta(days=today.weekday())
        start = this_mon - datetime.timedelta(days=7)
        end = this_mon - datetime.timedelta(days=1)
        return start.isoformat(), end.isoformat(), 7
    if re.search(r"今年", q):
        start = today.replace(month=1, day=1)
        return start.isoformat(), today.isoformat(), _span(start, today)
    if re.search(r"去年", q):
        y = today.year - 1
        start = datetime.date(y, 1, 1)
        end = datetime.date(y, 12, 31)
        return start.isoformat(), end.isoformat(), _span(start, end)
    if re.search(r"本月", q):
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat(), _span(start, today)
    if re.search(r"上月", q):
        first_this = today.replace(day=1)
        end = first_this - datetime.timedelta(days=1)
        start = end.replace(day=1)
        return start.isoformat(), end.isoformat(), _span(start, end)
    if re.search(r"最近|近期|这段时间", q):
        return (today - datetime.timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat(), None, DEFAULT_WINDOW_DAYS
    return (today - datetime.timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat(), None, DEFAULT_WINDOW_DAYS


_LOCAL_ALIASES = {
    "Global Partnership": "Global Partnership 团队",
    "GP": "Global Partnership 团队",
    "硅谷 BD": "硅谷 BD 团队",
    "硅谷BD": "硅谷 BD 团队",
    "Founder Park": "社群",
    "FounderPark": "社群",
    "Founder Park 团队": "社群",
    "Founder's Park": "社群",
    "FP": "社群",
    "内容侧": "编辑部",
    "编辑": "编辑部",
    "商业化": "商业化团队",
    "品牌": "品牌创意团队",
    "投资": "投资团队",
    "总裁办": "CEO / 总裁办",
    "播客": "音频播客团队",
    "播客团队": "音频播客团队",
    "视频号": "视频号团队",
}


def _alias_map() -> dict:
    alias_fn = getattr(db, "team_alias_map", None) or getattr(db, "_team_alias_map", None)
    if callable(alias_fn):
        try:
            return dict(alias_fn() or {})
        except Exception:
            pass
    return dict(_LOCAL_ALIASES)


def _alias_keys() -> list[str]:
    keys = list(_alias_map().keys()) + list(ingest.TEAMS)
    return sorted(set(keys), key=len, reverse=True)


def _normalize_team(name: str) -> str | None:
    fn = getattr(db, "normalize_team", None)
    if callable(fn):
        try:
            return fn(name)
        except Exception:
            pass
    t = (name or "").strip()
    if not t or t.startswith("→") or "→" in t:
        return None
    aliases = _alias_map()
    if t in aliases:
        t = aliases[t]
    if t in ingest.TEAMS:
        return t
    for canon in sorted(ingest.TEAMS, key=len, reverse=True):
        if canon in t or t in canon:
            return canon
    return None


def find_teams_in_question(q: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for key in _alias_keys():
        if not key or key in _ASK_ALIAS_BLOCK:
            continue
        # 屏蔽易误伤短别名；规范队名即使两字（如「社群」）仍保留
        if key not in ingest.TEAMS and len(key) <= 2:
            continue
        start = 0
        while True:
            i = q.find(key, start)
            if i < 0:
                break
            n = _normalize_team(key)
            if n:
                matches.append((i, i + len(key), n))
            start = i + 1
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    found, occupied = [], []
    for start, end, n in matches:
        if any(not (end <= a or start >= b) for a, b in occupied):
            continue
        if n not in found:
            found.append(n)
            occupied.append((start, end))
    return found


def _infer_section(q: str) -> str:
    if re.search(r"关注|话题|选题|关键词", q):
        return "关注"
    if re.search(r"看法|观点", q):
        return "看法"
    if re.search(r"关系|可同步|同步|重合|重叠|交集", q):
        return "关系"
    if re.search(r"接触|触达|聊过|见过|对接|拜访|联络|联系|认识", q):
        return "接触"
    return ""


def _parse_section_explicit(q: str) -> str:
    """计数问句里的 section：有明确词就用，没有则空（= 不按 section 过滤）。"""
    if re.search(r"关注|话题|选题", q):
        return "关注"
    if re.search(r"看法|观点", q):
        return "看法"
    if re.search(r"关系|合作|可同步", q):
        return "关系"
    if re.search(r"接触|触达|聊过|见过|对接|拜访|联络|联系|认识", q):
        return "接触"
    return ""


def _parse_count_kind(q: str) -> str | None:
    """计数对象：人 / 公司 / 主体(不限)。"""
    if re.search(r"多少人|几个人|多少位|几位|多少个人|人数", q):
        return "person"
    if re.search(r"多少家公司|几家|多少个公司|多少公司|多少家", q):
        return "company"
    if re.search(r"多少|几个|数量|总共多少", q):
        return "any"
    return None


def _diff_team_order(q: str, teams: list[str]) -> tuple[str, str] | None:
    if len(teams) < 2:
        return None
    aliases = _alias_map()
    labels: dict[str, list[str]] = {t: [t] for t in teams}
    for alias, canon in aliases.items():
        if canon in labels and alias not in labels[canon] and alias not in _ASK_ALIAS_BLOCK:
            labels[canon].append(alias)
    for t in teams:
        for key in _alias_keys():
            if key in _ASK_ALIAS_BLOCK:
                continue
            if _normalize_team(key) == t and key not in labels[t]:
                labels[t].append(key)
    neg = r"(还没|没有|尚未|没接触|未接触|未跟进)"
    bridge = r"[\s，,的]*"
    hits: list[tuple[int, str]] = []
    for t in teams:
        for lab in sorted(labels.get(t) or [t], key=len, reverse=True):
            for m in re.finditer(re.escape(lab) + bridge + neg, q):
                hits.append((m.start(), t))
                break
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])
    t_without = hits[0][1]
    other = next(x for x in teams if x != t_without)
    return other, t_without


def parse_intent(q: str) -> dict | None:
    q = (q or "").strip()
    if not q:
        return None
    date_from, date_to, window_days = parse_window(q)
    hardware = bool(re.search(r"硬件|" + "|".join(map(re.escape, HARDWARE_HINTS)), q))
    section = _infer_section(q)
    win = {"date_from": date_from, "date_to": date_to, "window_days": window_days}

    if re.search(r"各团队|分别知道|分别了解|各自知道|内部各", q):
        topic = ""
        m = re.search(r"(?:关于|针对)?\s*[「\"']?(.+?)[」\"']?\s*[，,]?\s*(?:公司内部)?各团队", q)
        if m:
            topic = m.group(1).strip(" ，,。的")
        if not topic:
            m = re.search(r"关于\s*(.+?)(?:，|,|公司|内部|各团队|分别)", q)
            if m:
                topic = m.group(1).strip()
        if not topic:
            m = re.search(
                r"(?:分别知道|分别了解|各自知道|各团队).{0,20}?(?:关于|针对)\s*[「\"']?(.+?)[」\"']?\s*$",
                q,
            )
            if m:
                topic = m.group(1).strip(" ，,。的？?")
        topic = (topic or "").strip()
        if topic and len(topic) >= 2 and topic not in _BAD_BY_TEAM_TOPICS and not re.fullmatch(r"什么.*", topic):
            return {"type": "by_team", "topic": topic, "hardware": False, **win}

    if re.search(r"海外", q) and re.search(r"国内", q) and re.search(r"没有|还没|尚未|没人|无部门", q):
        return {
            "type": "overseas_gap",
            "section": section if section in ("接触", "关注") else "接触",
            "hardware": hardware,
            **win,
        }

    teams = find_teams_in_question(q)
    neg = r"(还没|没有|尚未|没接触|未接触|未跟进)"
    classic_diff = bool(
        re.search(r"(接触过|跟进过|采访过|关注过|有记录).{0,40}但.{0,16}" + neg, q)
        or (re.search(r"但.{0,12}" + neg, q) and re.search(r"(接触过|跟进过|采访过)", q))
    )
    if classic_diff or (re.search(neg, q) and re.search(r"(接触过|跟进过|采访过)", q) and len(teams) >= 2):
        if len(teams) >= 2:
            ordered = _diff_team_order(q, teams)
            if ordered:
                team_a, team_b = ordered
                return {
                    "type": "diff",
                    "team_a": team_a,
                    "team_b": team_b,
                    "section": section if section in ("接触", "关注", "关系", "看法") else "接触",
                    "hardware": hardware,
                    **win,
                }
        if len(teams) == 1 and re.search(neg, q) and classic_diff:
            # 无法配对时不拦死用户，交给全文检索
            return None

    if re.search(r"同时也是|同时是|共同|都是|交集|两边都|双方都", q) or (
        re.search(r"也是", q) and re.search(r"采访对象|客户里|接触对象|跟进的客户", q)
    ):
        if len(teams) >= 2:
            return {
                "type": "intersect",
                "team_a": teams[0],
                "team_b": teams[1],
                "section": section if section in ("接触", "关注", "关系", "看法") else "接触",
                "hardware": hardware,
                **win,
            }

    return None


def _hardware_sql(alias: str = "") -> tuple[str, list]:
    pref = f"{alias}." if alias else ""
    ors = " OR ".join(
        f"({pref}group_title LIKE ? OR {pref}snippet LIKE ? OR {pref}name LIKE ?)"
        for _ in HARDWARE_HINTS
    )
    params = []
    for h in HARDWARE_HINTS:
        params.extend([f"%{h}%", f"%{h}%", f"%{h}%"])
    return f"({ors})", params


def _rows_to_contexts(rows: list, limit: int = RESULT_LIMIT) -> tuple[list[dict], int]:
    total = len(rows)
    ctxs = []
    for r in rows[:limit]:
        ctxs.append({
            "期号": r["issue_slug"],
            "章节": r.get("section") or "",
            "标题": r["name"],
            "内容": " · ".join(filter(None, [
                f"团队 {r['team']}",
                r.get("group_title") or "",
                (r.get("snippet") or "")[:400],
                f"来源提示 {r['source_hint']}" if r.get("source_hint") else "",
            ])),
            "团队": r["team"],
        })
    return ctxs, total


def query_count(
    con,
    team: str,
    date_from: str | None,
    date_to: str | None,
    *,
    section: str = "",
    kind: str = "any",
    limit: int = RESULT_LIMIT,
) -> tuple[list[dict], int]:
    """团队在时间窗内的主体计数：直接 SQL 精确计数，不让 LLM 从上下文里数。

    section 为空 = 不按 section 过滤（「接触了多少人」没明说时，避免漏算）。

    kind 与数据现实（重要）：
      entity_team_facts.kind 只有 company / topic / team / unknown，**从不产生 person**
      （见 db._infer_kind：接触段恒为 company/unknown）。所以「接触了多少人」不能按
      kind='person' 过滤——那必然返回 0，比近似回答更糟。这里把「人」问法按主体计数，
      并在口径里明说事实表未区分个人。

    去重口径：同一主体可能以「A / B」「X（Y）」复合名出现，也可能单独出现。
    若按拆分别名各自计数会把一个主体算成多个（虚高），故先取全量原子别名做并集，
    再让每个主体认领一个代表键，按代表键去重。
    """
    if not team:
        return [], 0
    a_date, a_params = _date_clause("", date_from, date_to)
    where = ["team = ?"]
    params: list = [team]
    if a_date:
        where.append(a_date.lstrip(" AND"))
        params.extend(a_params)
    if section:
        where.append("section = ?")
        params.append(section)
    # 只在明确要「公司」时过滤 kind；person 不可信（表里没有），故不过滤
    if kind == "company":
        where.append("kind = 'company'")
    sql = (
        "SELECT name, kind, section, date_end, issue_slug FROM entity_team_facts "
        f"WHERE {' AND '.join(where)}"
    )

    rows = [dict(r) for r in con.execute(sql, params)]

    # 去重口径（真数据实测得出）：
    #  - 同一主体可能以复合名出现，如「灵瑙科技 / 飞声助听器 / 亲宝宝」「擎羽科技（晴雨科技）」
    #  - 若按拆分别名各自计数 → 虚高（硅谷 13 行会算成 17 个）
    #  - 若只按原始名计数 → 偏低（「A / B」一格确实列了 2 家）
    # 故 headline 用「原始主体名去重」这个最可辩护的口径，并同时报出拆分后的数量。
    groups: dict[str, dict] = {}
    for r in rows:
        key = _entity_norm(r["name"]) or (r["name"] or "").strip()
        if not key:
            continue
        g = groups.get(key)
        if g is None:
            groups[key] = {"name": r["name"], "kind": r["kind"], "n": 1}
        else:
            g["n"] += 1

    total = len(groups)
    expanded_keys: set[str] = set()
    for r in rows:
        expanded_keys |= _entity_keys(r["name"])
    expanded = len(expanded_keys)

    ranked = sorted(groups.values(), key=lambda g: (-g["n"], g["name"]))
    if kind == "person":
        kind_label = "个主体"
        caveat = ("注意：事实表「接触」段记录的是公司/机构主体，未区分个人；"
                  "因此这是主体数，不是精确人数。")
    elif kind == "company":
        kind_label = "家公司"
        caveat = ""
    else:
        kind_label = "个主体"
        caveat = ""
    multi = ""
    if expanded > total:
        multi = f"（其中部分条目一格列出多个主体，拆开计为 {expanded} 个。）"
    ctxs = [{
        "期号": "查询说明",
        "章节": "结构化计数",
        "标题": "本答案由主体×团队事实表精确计数，非全文模糊检索",
        "内容": (
            f"团队「{team}」在 {_window_label({'date_from': date_from, 'date_to': date_to, 'window_days': 0})} "
            f"范围内{'（' + section + '）' if section else ''}共涉及 {total} {kind_label}"
            f"（按记录中的主体名去重）。{multi}{caveat}"
            "请直接给出这个数字，并说明统计口径；不要用下列示例去反推或编造其他数字。"
        ),
    }]
    for g in ranked[:limit]:
        ctxs.append({
            "期号": "",
            "章节": "计数明细",
            "标题": g["name"],
            "内容": f"团队 {team} · 记录 {g['n']} 条 · 类型 {g['kind'] or 'unknown'}",
            "团队": team,
        })
    return ctxs, total


def _date_clause(alias: str, date_from: str | None, date_to: str | None = None) -> tuple[str, list]:
    if not date_from and not date_to:
        return "", []
    pref = f"{alias}." if alias else ""
    expr = f"COALESCE(NULLIF({pref}date_end,''), NULLIF({pref}date_start,''))"
    parts, params = [f"({expr} IS NOT NULL AND {expr} != '')"], []
    if date_from:
        parts.append(f"{expr} >= ?")
        params.append(date_from)
    if date_to:
        parts.append(f"{expr} <= ?")
        params.append(date_to)
    return " AND " + " AND ".join(parts), params


def _entity_norm(name: str) -> str:
    """主体名归一：去空白/常见后缀，用于交集差集匹配。"""
    s = re.sub(r"\s+", "", (name or "").strip())
    for suf in ("股份有限公司", "有限公司", "公司", "集团"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s.lower()


# 复合主体名拆分：事实表里同一行常塞多个主体（"A / B / C"、"A、B"、"X · Y"、
# "X：说明文字"、"X（Y）"）。精确等值匹配会整串落空 → 交集/差集漏判。
_COMPOSITE_SEP_RE = re.compile(r"[／/、|｜]")
_TITLE_DOT_RE = re.compile(r"[·•]")
_LIST_PREFIX_RE = re.compile(r"^(?:等|以及|和|与)\s*")
_COLON_TAIL_RE = re.compile(r"[：:].*$")
_PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")


def _split_entity_aliases(name: str) -> list[str]:
    """把复合主体名拆成原子别名候选（含原始整串，整串放最后）。

    "灵瑙科技 / 飞声助听器 / 亲宝宝 / 氧刻 / 影眸科技"
        → ["灵瑙科技", "飞声助听器", "亲宝宝", "氧刻", "影眸科技", 原串]
    "刘靖康（影石 Insta360）：编辑部接触、视频号出镜"
        → ["刘靖康", "影石 Insta360", 原串]
    "擎羽科技（晴雨科技）" → ["擎羽科技", "晴雨科技", 原串]
    """
    raw = (name or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    for chunk in _COMPOSITE_SEP_RE.split(raw):
        for sub in _TITLE_DOT_RE.split(chunk):
            sub = _LIST_PREFIX_RE.sub("", sub.strip())
            sub = _COLON_TAIL_RE.sub("", sub).strip()
            # 括号内容也是候选别名（通常是公司/全称），如 "擎羽科技（晴雨科技）"
            for m in _PAREN_RE.finditer(sub):
                inner = _LIST_PREFIX_RE.sub("", m.group(1).strip()).strip()
                if len(inner) >= 2:
                    parts.append(inner)
            sub = _PAREN_RE.sub("", sub).strip()
            if len(sub) >= 2:
                parts.append(sub)
    parts.append(raw)
    return list(dict.fromkeys(parts))


def _entity_keys(name: str) -> set[str]:
    """主体名的全部匹配 key（含复合串拆分），intersect/diff 匹配用。"""
    keys = {_entity_norm(p) for p in _split_entity_aliases(name)}
    keys.discard("")
    return keys


def _fetch_team_section_rows(
    con,
    team: str,
    section: str,
    date_from: str | None,
    date_to: str | None,
    *,
    hardware: bool = False,
) -> list[dict]:
    hw_sql, hw_params = ("", [])
    if hardware:
        hw_sql, hw_params = _hardware_sql("")
        hw_sql = " AND " + hw_sql
    a_date, a_params = _date_clause("", date_from, date_to)
    sql = f"""
    SELECT name, team, issue_slug, section, group_title, snippet, source_hint, date_end
    FROM entity_team_facts
    WHERE team = ? AND section = ?{a_date.replace('a.', '')}{hw_sql}
    ORDER BY date_end DESC, name
    """
    params = [team, section, *a_params, *hw_params]
    return [dict(r) for r in con.execute(sql, params)]


def query_diff(con, team_a: str, team_b: str, date_from: str | None, section: str = "接触",
               hardware: bool = False, date_to: str | None = None) -> tuple[list[dict], int]:
    rows_a = _fetch_team_section_rows(con, team_a, section, date_from, date_to, hardware=hardware)
    keys_b: set[str] = set()
    for r in _fetch_team_section_rows(con, team_b, section, date_from, date_to, hardware=hardware):
        keys_b |= _entity_keys(r["name"])
    seen, uniq = set(), []
    for r in rows_a:
        ks = _entity_keys(r["name"])
        if not ks or (ks & keys_b) or (ks & seen):
            continue
        seen |= ks
        uniq.append(r)
    return _rows_to_contexts(uniq)


def query_intersect(con, team_a: str, team_b: str, date_from: str | None, section: str = "接触",
                    hardware: bool = False, date_to: str | None = None) -> tuple[list[dict], int]:
    rows_a = _fetch_team_section_rows(con, team_a, section, date_from, date_to, hardware=hardware)
    keys_b: set[str] = set()
    for r in _fetch_team_section_rows(con, team_b, section, date_from, date_to, hardware=hardware):
        keys_b |= _entity_keys(r["name"])
    seen, uniq = set(), []
    for r in rows_a:
        ks = _entity_keys(r["name"])
        if not ks or not (ks & keys_b) or (ks & seen):
            continue
        seen |= ks
        uniq.append(r)
    ctxs, total = _rows_to_contexts(uniq)
    for c in ctxs:
        c["内容"] = f"同时出现在 {team_a} 与 {team_b} · " + c["内容"]
    return ctxs, total


def query_overseas_gap(con, date_from: str | None, section: str = "接触",
                       hardware: bool = False, date_to: str | None = None) -> tuple[list[dict], int]:
    o_teams = overseas_teams()
    d_teams = domestic_teams()
    if not o_teams or not d_teams:
        return [], 0
    keys_domestic: set[str] = set()
    for dt in d_teams:
        for r in _fetch_team_section_rows(con, dt, section, date_from, date_to, hardware=hardware):
            keys_domestic |= _entity_keys(r["name"])
    seen, uniq = set(), []
    for ot in o_teams:
        for r in _fetch_team_section_rows(con, ot, section, date_from, date_to, hardware=hardware):
            ks = _entity_keys(r["name"])
            if not ks or (ks & keys_domestic) or (ks & seen):
                continue
            seen |= ks
            uniq.append(r)
    return _rows_to_contexts(uniq)


# ---- 2 跳图查询：基于 item_entity_facts（条目↔实体边表）做共现/桥接 ----

def _like_entity_clause(alias: str, term: str, dialect: str = "") -> tuple[str, list]:
    """种子实体匹配：复合串也要能命中（LIKE 双向包含），如「影眸科技」命中复合行。

    PG(psycopg2) 会把 SQL 里的字面 `%` 当参数占位符，须写成 `%%`。
    """
    t = (term or "").strip()
    pct = "%%" if dialect == "postgresql" else "%"
    return (
        f"({alias}.entity_name LIKE ? OR ? LIKE '{pct}' || {alias}.entity_name || '{pct}')",
        [f"%{t}%", t],
    )


def _graph_rows_to_contexts(rows: list[dict], *, limit: int = 20) -> tuple[list[dict], int]:
    """2 跳结果 → contexts。每行是一个共现/桥接主体（不是条目）。

    注意：不展示 entity_kind —— 现有 infer_entity_kind 启发式噪声大
    （公司常被误标 person），展示会给 LLM 错误信号。
    """
    total = len(rows)
    ctxs = []
    for r in rows[:limit]:
        co = int(r.get("co") or 0)
        ctxs.append({
            "期号": r.get("date_end") or "",
            "章节": "图检索",
            "标题": r["name"],
            "内容": " · ".join(filter(None, [
                f"团队 {r.get('team') or '未知'}",
                f"关联 {co} 条记录",
                (r.get("snippet") or "")[:300],
                f"来源 {r['source_hint']}" if r.get("source_hint") else "",
            ])),
            "团队": r.get("team") or "",
        })
    return ctxs, total


def query_cooccur(
    con,
    seed: str,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """2 跳共现：seed 实体 → 同条目出现的其他实体（按共现次数排序）。

    问法示例：「跟面壁智能聊过的人还接触过谁」「面壁智能都和谁一起出现」。
    返回主体（不是条目），每行 = 一个共现实体。
    """
    seed = (seed or "").strip()
    if not seed:
        return [], 0
    dialect = getattr(con, "dialect", "")
    seed_sql, seed_params = _like_entity_clause("e1", seed, dialect)
    e2_sql, e2_params = _like_entity_clause("e2", seed, dialect)
    date_sql, date_params = _date_clause("e1", date_from, date_to)
    sql = f"""
    SELECT e2.entity_name AS name,
           MIN(e2.owner_team) AS team,
           MAX(e2.entity_kind) AS kind,
           MAX(e2.date_end) AS date_end,
           MAX(e2.text_snippet) AS snippet,
           MAX(e2.source_label) AS source_hint,
           COUNT(DISTINCT e1.item_id) AS co
    FROM item_entity_facts e1
    JOIN item_entity_facts e2 ON e1.item_id = e2.item_id
    WHERE {seed_sql}
      AND e2.entity_name <> e1.entity_name
      AND NOT ({e2_sql})
      {date_sql}
    GROUP BY e2.entity_name
    ORDER BY co DESC, name
    LIMIT ?
    """
    params = [*seed_params, *e2_params, *date_params, int(limit)]
    rows = [dict(r) for r in con.execute(sql, params)]
    return _graph_rows_to_contexts(rows, limit=limit)


def query_bridge(
    con,
    a: str,
    b: str,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """2 跳桥接：同时与 a、b 共现过的中间实体（「谁把 X 和 Y 连起来」）。

    a 与 b 不必出现在同一条目：中间实体只要分别与二者共现过即可（这正是「桥梁」）。
    """
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return [], 0
    dialect = getattr(con, "dialect", "")
    a_x_sql, a_x_p = _like_entity_clause("x", a, dialect)
    a_e_sql, a_e_p = _like_entity_clause("e", a, dialect)
    b_y_sql, b_y_p = _like_entity_clause("y", b, dialect)
    b_e_sql, b_e_p = _like_entity_clause("e", b, dialect)
    date_sql, date_params = _date_clause("e", date_from, date_to)
    sql = f"""
    WITH ca AS (
        SELECT DISTINCT e.entity_name AS name, e.item_id
        FROM item_entity_facts e
        JOIN item_entity_facts x ON x.item_id = e.item_id AND {a_x_sql}
        WHERE NOT ({a_e_sql}) {date_sql}
    ),
    cb AS (
        SELECT DISTINCT e.entity_name AS name, e.item_id
        FROM item_entity_facts e
        JOIN item_entity_facts y ON y.item_id = e.item_id AND {b_y_sql}
        WHERE NOT ({b_e_sql}) {date_sql}
    ),
    hits AS (
        SELECT ca.name AS name, COUNT(*) AS co
        FROM ca JOIN cb ON ca.name = cb.name
        GROUP BY ca.name
    )
    SELECT h.name AS name, h.co AS co,
           MIN(e.owner_team) AS team,
           MAX(e.entity_kind) AS kind,
           MAX(e.date_end) AS date_end,
           MAX(e.text_snippet) AS snippet,
           MAX(e.source_label) AS source_hint
    FROM hits h JOIN item_entity_facts e ON e.entity_name = h.name
    GROUP BY h.name, h.co
    ORDER BY h.co DESC, h.name
    LIMIT ?
    """
    params = [*a_x_p, *a_e_p, *date_params, *b_y_p, *b_e_p, *date_params, int(limit)]
    rows = [dict(r) for r in con.execute(sql, params)]
    return _graph_rows_to_contexts(rows, limit=limit)


def query_by_team(con, topic: str, date_from: str | None, date_to: str | None = None,
                  team_scope: str = "") -> tuple[list[dict], int]:
    like = f"%{topic}%"
    date_sql, date_params = "", []
    if date_from or date_to:
        expr = "COALESCE(NULLIF(date_end,''), NULLIF(date_start,''))"
        parts = [f"({expr} IS NOT NULL AND {expr} != '')"]
        if date_from:
            parts.append(f"{expr} >= ?"); date_params.append(date_from)
        if date_to:
            parts.append(f"{expr} <= ?"); date_params.append(date_to)
        date_sql = " AND ".join(parts) + " AND "
    team_sql, team_params = "", []
    if team_scope:
        team_sql = "team = ? AND "
        team_params = [team_scope]
    sql = f"""
    SELECT name, team, issue_slug, section, group_title, snippet, source_hint, date_end
    FROM entity_team_facts
    WHERE {date_sql}{team_sql}(name LIKE ? OR snippet LIKE ? OR group_title LIKE ?)
    ORDER BY team, date_end DESC, name
    """
    rows = [dict(r) for r in con.execute(sql, (*date_params, *team_params, like, like, like))]
    return _rows_to_contexts(rows, limit=RESULT_LIMIT)


def _filter_contexts_team(ctxs: list[dict], team_scope: str) -> list[dict]:
    if not team_scope:
        return ctxs
    out: list[dict] = []
    for c in ctxs:
        if c.get("期号") == "查询说明":
            out.append(c)
            continue
        blob = f"{c.get('标题', '')} {c.get('内容', '')}"
        if team_scope in blob:
            out.append(c)
    return out


def build_structured_preamble(intent: dict, total: int, shown: int) -> dict:
    t = intent.get("type")
    win = intent.get("window_days", DEFAULT_WINDOW_DAYS)
    df = intent.get("date_from") or ""
    dt = intent.get("date_to") or ""
    if not df and not dt:
        win_txt = "不限时间"
    elif dt:
        win_txt = f"{df or '…'} 至 {dt}（约 {win} 天，闭区间）"
    else:
        win_txt = f"{df} 起至今（约 {win} 天）"
    if t == "diff":
        desc = (f"查询类型：差集。时间范围：{win_txt}。"
                f"团队 A「{intent['team_a']}」在该范围内有记录、团队 B「{intent['team_b']}」在该范围内无记录。"
                f"共 {total} 个主体，下列展示 {shown} 个。注意：B 无记录仅指本时间范围内。")
    elif t == "intersect":
        desc = (f"查询类型：交集。时间范围：{win_txt}。"
                f"同时出现在「{intent['team_a']}」与「{intent['team_b']}」。共 {total} 个，展示 {shown} 个。")
    elif t == "overseas_gap":
        desc = (f"查询类型：海外有接触、国内团队在该范围内无跟进。时间范围：{win_txt}。"
                f"海外团队={','.join(overseas_teams())}；国内团队={','.join(domestic_teams())}。"
                f"共 {total} 个，展示 {shown} 个。")
    elif t == "by_team":
        desc = (f"查询类型：按团队聚合。主题「{intent.get('topic')}」。"
                f"时间范围：{win_txt}。共 {total} 条，展示 {shown} 条。请按团队分别陈述。")
    elif t == "cooccur":
        desc = (f"查询类型：二跳共现。种子主体「{intent.get('seed')}」。时间范围：{win_txt}。"
                f"下列主体与种子主体出现在同一条目里（按关联记录条数排序，多者在前）。"
                f"共 {total} 个，展示 {shown} 个。")
    elif t == "bridge":
        desc = (f"查询类型：二跳桥接。下列主体分别与「{intent.get('seed')}」和"
                f"「{intent.get('seed_b')}」都有关联（是二者的中间连接点）。"
                f"时间范围：{win_txt}。共 {total} 个，展示 {shown} 个。")
    elif t == "count":
        kind_label = {"person": "个主体（事实表未区分个人）", "company": "家公司", "any": "个主体"}.get(
            intent.get("kind") or "any", "个主体"
        )
        desc = (f"查询类型：精确计数。团队「{intent.get('team')}」"
                f"{'· ' + intent.get('section') if intent.get('section') else ''}，"
                f"时间范围：{win_txt}，按主体名去重后共 {total} {kind_label}。"
                f"下列 {shown} 个是明细示例，不是全部。请直接回答 {total}，不要另算。")
    else:
        desc = f"结构化查询结果共 {total} 条，展示 {shown} 条。"
    if intent.get("hardware"):
        desc += "已按硬件相关词弱过滤。"
    return {
        "期号": "查询说明",
        "章节": "结构化检索",
        "标题": "本答案由主体×团队事实表算出，非全文模糊检索",
        "内容": desc + " 请只根据下列结果回答；若列表为空，明确说该时间范围内没有符合条件的记录。不要编造未列出的主体。",
    }


def _window_label(intent: dict) -> str:
    df = intent.get("date_from") or ""
    dt = intent.get("date_to") or ""
    win = intent.get("window_days", DEFAULT_WINDOW_DAYS)
    if not df and not dt:
        return "不限时间"
    if dt:
        return f"{df or '…'} 至 {dt}（约 {win} 天）"
    return f"{df} 起至今（约 {win} 天）"


def empty_result_answer(intent: dict) -> str:
    """结构化查询零命中：固定话术，不调用 LLM。"""
    t = intent.get("type") or ""
    win_txt = _window_label(intent)
    teams = intent.get("teams") if isinstance(intent.get("teams"), list) else []
    team_a = intent.get("team_a") or (teams[0] if len(teams) > 0 else None) or "团队A"
    team_b = intent.get("team_b") or (teams[1] if len(teams) > 1 else None) or "团队B"
    if t == "diff":
        body = (
            f"在 {win_txt} 范围内，未找到符合以下条件的主体："
            f"「{team_a}」有记录，且「{team_b}」在该范围内无记录。"
        )
    elif t == "intersect":
        body = (
            f"在 {win_txt} 范围内，未找到同时出现在"
            f"「{team_a}」与「{team_b}」的主体。"
        )
    elif t == "overseas_gap":
        body = (
            f"在 {win_txt} 范围内，未找到「海外团队有接触、国内团队无跟进」的主体。"
        )
    elif t == "by_team":
        topic = intent.get("topic") or "该主题"
        body = f"在 {win_txt} 范围内，未找到与「{topic}」相关的团队记录。"
    elif t == "cooccur":
        body = (f"在 {win_txt} 范围内，未找到与「{intent.get('seed')}」"
                f"出现在同一条目里的其他主体。")
    elif t == "bridge":
        body = (f"在 {win_txt} 范围内，未找到同时与「{intent.get('seed')}」和"
                f"「{intent.get('seed_b')}」共现过的中间主体。")
    elif t == "count":
        kind_label = {"person": "个主体（事实表未区分个人）", "company": "家公司", "any": "个主体"}.get(
            intent.get("kind") or "any", "个主体"
        )
        body = (f"在 {win_txt} 范围内，团队「{intent.get('team')}」"
                f"{'（' + intent.get('section') + '）' if intent.get('section') else ''}"
                f"没有任何记录，计数为 0 {kind_label}。")
    else:
        body = f"在 {win_txt} 范围内，结构化检索未返回任何记录。"
    return body + "\n\n来源：结构化检索（主体×团队事实表）"


def run_structured(con, intent: dict, team_scope: str = "") -> dict:
    if intent.get("type") == "error":
        return {"ok": False, "mode": "structured", "contexts": [], "total": 0,
                "intent": intent, "message": intent.get("message") or "无法解析该交叉问题"}

    t = intent["type"]
    df, dt = intent.get("date_from"), intent.get("date_to")
    if t == "diff":
        ctxs, total = query_diff(
            con, intent["team_a"], intent["team_b"], df,
            intent.get("section") or "接触", intent.get("hardware", False), dt,
        )
    elif t == "intersect":
        ctxs, total = query_intersect(
            con, intent["team_a"], intent["team_b"], df,
            intent.get("section") or "接触", intent.get("hardware", False), dt,
        )
    elif t == "overseas_gap":
        ctxs, total = query_overseas_gap(
            con, df, intent.get("section") or "接触", intent.get("hardware", False), dt,
        )
    elif t == "by_team":
        ctxs, total = query_by_team(con, intent["topic"], df, dt, team_scope=team_scope)
    elif t == "cooccur":
        ctxs, total = query_cooccur(con, intent.get("seed") or "", df, dt)
    elif t == "bridge":
        ctxs, total = query_bridge(con, intent.get("seed") or "", intent.get("seed_b") or "", df, dt)
    elif t == "count":
        ctxs, total = query_count(
            con,
            intent.get("team") or "",
            df,
            dt,
            section=intent.get("section") or "",
            kind=intent.get("kind") or "any",
        )
    else:
        return {"ok": False, "mode": "structured", "contexts": [], "total": 0,
                "intent": intent, "message": "未知查询类型"}

    ctxs = _filter_contexts_team(ctxs, team_scope)
    if team_scope and t != "by_team":
        total = len(ctxs)

    preamble = build_structured_preamble(intent, total, len(ctxs))
    return {
        "ok": True,
        "mode": "structured",
        "contexts": [preamble] + ctxs,
        "total": total,
        "intent": {
            "type": t,
            "team_a": intent.get("team_a"),
            "team_b": intent.get("team_b"),
            "teams": [intent.get("team_a"), intent.get("team_b")],
            "team": intent.get("team"),
            "topic": intent.get("topic"),
            "seed": intent.get("seed"),
            "seed_b": intent.get("seed_b"),
            "section": intent.get("section"),
            "kind": intent.get("kind"),
            "window_days": intent.get("window_days"),
            "date_from": intent.get("date_from"),
            "date_to": intent.get("date_to"),
            "hardware": intent.get("hardware", False),
        },
    }
