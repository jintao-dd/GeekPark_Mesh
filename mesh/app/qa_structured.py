"""跨部门交叉问答：意图分流 + 事实表 SQL（差集/交集/海外缺口/按团队聚合）。"""
from __future__ import annotations
import os, re, datetime
from . import db, ingest

DEFAULT_WINDOW_DAYS = int(
    os.environ.get("MESH_QA_WINDOW_DAYS")
    or os.environ.get("MESH_QA_LEXICAL_WINDOW_DAYS")
    or "90"
)
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
    m = re.search(r"近\s*(\d+)\s*个?月", q)
    if m:
        days = int(m.group(1)) * 30
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
    if re.search(r"关系|可同步", q):
        return "关系"
    return "接触"


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
    keys_b = {_entity_norm(r["name"]) for r in _fetch_team_section_rows(
        con, team_b, section, date_from, date_to, hardware=hardware,
    )}
    seen, uniq = set(), []
    for r in rows_a:
        k = _entity_norm(r["name"])
        if not k or k in keys_b or k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return _rows_to_contexts(uniq)


def query_intersect(con, team_a: str, team_b: str, date_from: str | None, section: str = "接触",
                    hardware: bool = False, date_to: str | None = None) -> tuple[list[dict], int]:
    rows_a = _fetch_team_section_rows(con, team_a, section, date_from, date_to, hardware=hardware)
    keys_b = {_entity_norm(r["name"]) for r in _fetch_team_section_rows(
        con, team_b, section, date_from, date_to, hardware=hardware,
    )}
    seen, uniq = set(), []
    for r in rows_a:
        k = _entity_norm(r["name"])
        if not k or k not in keys_b or k in seen:
            continue
        seen.add(k)
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
            keys_domestic.add(_entity_norm(r["name"]))
    seen, uniq = set(), []
    for ot in o_teams:
        for r in _fetch_team_section_rows(con, ot, section, date_from, date_to, hardware=hardware):
            k = _entity_norm(r["name"])
            if not k or k in keys_domestic or k in seen:
                continue
            seen.add(k)
            uniq.append(r)
    return _rows_to_contexts(uniq)


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
            "topic": intent.get("topic"),
            "section": intent.get("section"),
            "window_days": intent.get("window_days"),
            "date_from": intent.get("date_from"),
            "date_to": intent.get("date_to"),
            "hardware": intent.get("hardware", False),
        },
    }
