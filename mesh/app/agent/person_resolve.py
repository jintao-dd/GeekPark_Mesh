"""Person resolve — 简称 / 工号 / 会话提及 → 通讯录全名。

这是 Company Understanding 的人名词典层，不是周报向量 Memory。
用于检索前扩展：锦涛→杜锦涛，思琪→赵思琪，49→彭康林。

别名手填表：app/agent/data/company_people_roster.json
（按团队/职位列出全员；aliases 可多人多别名）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("uvicorn.error")

# 内置兜底；正式别名以 roster JSON 为准（可覆盖）
SEED_ALIASES: dict[str, str] = {
    "49": "彭康林",
    "peng49": "彭康林",
    "锦涛": "杜锦涛",
    "思琪": "赵思琪",
    "lilyann": "赵思琪",
    "lilyanna": "赵思琪",
    "山山": "张山山",
    "康林": "彭康林",
    "晓龙": "闫晓龙",
    "靖玉": "靖宇",
}

_ROSTER_PATH = Path(__file__).resolve().parent / "data" / "company_people_roster.json"
_ROSTER_CACHE: dict[str, Any] | None = None

_STOP = frozenset(
    {
        "我们",
        "你们",
        "他们",
        "什么",
        "怎么",
        "哪些",
        "最近",
        "周报",
        "日历",
        "群聊",
        "成员",
        "详情",
        "关联",
        "事情",
        "帮助",
        "列出",
        "可以",
        "访问",
        "人员",
        "日程",
        "梳理",
        "相关",
        "内容",
        "同事",
        "飞书",
        "已上线",
        "值得",
        "关注",
        "还有",
        "以及",
        "一下",
        "这个",
        "那个",
        "自己",
        "今天",
        "明天",
        "后天",
        "上午",
        "下午",
        "晚上",
        "老师",
        # 「老板」是张鹏等手填别名，不能当停用词，否则永远抽不出 token
        "小姐",
        "先生",
    }
)

# 称呼尾巴：万老师→万；靖宇姐→靖宇
_HONORIFIC_TAIL = re.compile(
    r"(?:老师|姐姐|哥哥|大姐|大哥|总|姐|哥|总工|总助)$"
)

_CJK_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,4}")
_CODE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]*\d{1,6}|\d{1,6})(?![A-Za-z0-9_])")
_LATIN_NICK = re.compile(r"(?<![A-Za-z])([A-Za-z]{2,12})(?![A-Za-z])")


@dataclass
class PersonHit:
    alias: str
    canonical: str
    open_id: str = ""
    employee_no: str = ""
    score: float = 0.0
    source: str = ""  # seed|org_exact|org_suffix|org_emp|session|mention

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolveResult:
    hits: list[PersonHit] = field(default_factory=list)
    expanded_query: str = ""
    unresolved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [h.to_dict() for h in self.hits],
            "expanded_query": self.expanded_query,
            "unresolved": list(self.unresolved),
        }

    def canonical_names(self) -> list[str]:
        out: list[str] = []
        for h in self.hits:
            if h.canonical and h.canonical not in out:
                out.append(h.canonical)
        return out


def _session_people(session: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if session is None:
        return out
    for m in getattr(session, "last_mentions", None) or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        oid = str(m.get("open_id") or "").strip()
        if name:
            out.append({"name": name, "open_id": oid, "employee_no": "", "source": "mention"})
    for name in filter_known_people(getattr(session, "active_entities", None) or []):
        if name and not any(x["name"] == name for x in out):
            out.append({"name": name, "open_id": "", "employee_no": "", "source": "session"})
    return out


def load_roster(*, force: bool = False) -> dict[str, Any]:
    """加载手填别名表；失败返回空结构。"""
    global _ROSTER_CACHE
    if _ROSTER_CACHE is not None and not force:
        return _ROSTER_CACHE
    data: dict[str, Any] = {"people": [], "alias_to_name": {}}
    try:
        if _ROSTER_PATH.is_file():
            raw = json.loads(_ROSTER_PATH.read_text(encoding="utf-8"))
            people = raw.get("people") if isinstance(raw, dict) else []
            if not isinstance(people, list):
                people = []
            alias_map: dict[str, str] = {}
            for row in people:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                for a in row.get("aliases") or []:
                    alias = str(a or "").strip()
                    if not alias or alias == name:
                        continue
                    # 后写不覆盖先写：避免重名简称互踩（手填时请保证唯一）
                    alias_map.setdefault(alias, name)
                    alias_map.setdefault(alias.lower(), name)
            data = {
                "people": people,
                "alias_to_name": alias_map,
                "path": str(_ROSTER_PATH),
            }
    except Exception as e:
        log.warning("person_resolve roster load failed: %s", e)
    _ROSTER_CACHE = data
    return data


def alias_map() -> dict[str, str]:
    """内置种子 ∪ roster 手填别名（roster 优先）。"""
    out = dict(SEED_ALIASES)
    roster = load_roster()
    for k, v in (roster.get("alias_to_name") or {}).items():
        if k and v:
            out[str(k)] = str(v)
    return out


def _roster_people() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in load_roster().get("people") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "open_id": str(row.get("open_id") or "").strip(),
                "employee_no": str(row.get("employee_no") or "").strip(),
                "job_title": str(row.get("job_title") or "").strip(),
                "teams": ",".join(row.get("teams") or []),
                "source": "roster",
            }
        )
    return out


def _org_people() -> list[dict[str, str]]:
    out = _roster_people()  # 离线/无 Hands 时也有全员表
    try:
        from .feishu_hands import org_directory

        _depts, people = org_directory.load_directory()
        by_name = {p["name"]: p for p in out}
        for u in people or []:
            if not isinstance(u, dict):
                continue
            name = str(u.get("name") or "").strip()
            if not name:
                continue
            row = {
                "name": name,
                "open_id": str(u.get("open_id") or "").strip(),
                "employee_no": str(u.get("employee_no") or "").strip(),
                "source": "org",
            }
            # org 实时字段优先
            if name in by_name:
                prev = by_name[name]
                if row["open_id"]:
                    prev["open_id"] = row["open_id"]
                if row["employee_no"]:
                    prev["employee_no"] = row["employee_no"]
                prev["source"] = "org+roster"
            else:
                out.append(row)
                by_name[name] = row
        return out
    except Exception as e:
        log.info("person_resolve org unavailable: %s", e)
        return out


def candidate_tokens(text: str) -> list[str]:
    """抽出可能人名/工号；中文按 2～4 字滑窗，并剥老师/姐等称呼。"""
    q = text or ""
    found: list[str] = []

    def _add(tok: str) -> None:
        if not tok or tok in _STOP or tok in found:
            return
        found.append(tok)

    for m in re.finditer(r"[\u4e00-\u9fff]+", q):
        run = m.group(0)
        stripped = _HONORIFIC_TAIL.sub("", run)
        for piece in (run, stripped):
            if not piece:
                continue
            n = len(piece)
            if 1 <= n <= 4:
                _add(piece)
            for L in (2, 3, 4):
                if n < L:
                    continue
                for i in range(0, n - L + 1):
                    _add(piece[i : i + L])
    for m in _CODE_TOKEN.finditer(q):
        _add(m.group(1))
    for m in _LATIN_NICK.finditer(q):
        _add(m.group(1))
        _add(m.group(1).lower())
    return found


def _match_token(token: str, people: list[dict[str, str]]) -> PersonHit | None:
    t = (token or "").strip()
    if not t:
        return None
    # 再剥一次称呼
    t2 = _HONORIFIC_TAIL.sub("", t).strip()
    if t2 and t2 != t:
        hit = _match_token(t2, people)
        if hit:
            return PersonHit(
                alias=token,
                canonical=hit.canonical,
                open_id=hit.open_id,
                employee_no=hit.employee_no,
                score=hit.score * 0.99,
                source=hit.source + "+honorific",
            )
    tl = t.lower()

    # 1) seed alias ∪ roster 手填
    amap = alias_map()
    seed = amap.get(t) or amap.get(tl)
    if seed:
        for p in people:
            if p["name"] == seed:
                return PersonHit(
                    alias=t,
                    canonical=seed,
                    open_id=p.get("open_id") or "",
                    employee_no=p.get("employee_no") or "",
                    score=0.99,
                    source="roster_alias" if t in (load_roster().get("alias_to_name") or {}) else "seed",
                )
        return PersonHit(alias=t, canonical=seed, score=0.95, source="seed")

    # 2) exact full name / latin name case-insensitive
    exact = [
        p
        for p in people
        if p["name"] == t or p["name"].lower() == tl
    ]
    if len(exact) == 1:
        p = exact[0]
        return PersonHit(
            alias=t,
            canonical=p["name"],
            open_id=p.get("open_id") or "",
            employee_no=p.get("employee_no") or "",
            score=1.0,
            source="org_exact",
        )

    # 3) employee_no / 工号
    emp = [p for p in people if (p.get("employee_no") or "").lower() == tl]
    if len(emp) == 1:
        p = emp[0]
        return PersonHit(
            alias=t,
            canonical=p["name"],
            open_id=p.get("open_id") or "",
            employee_no=p.get("employee_no") or "",
            score=0.98,
            source="org_emp",
        )

    # 4) 单姓唯一：万老师→万→万东峰（通讯录仅一人姓万）
    if re.fullmatch(r"[\u4e00-\u9fff]", t):
        surname = [p for p in people if p["name"].startswith(t) and len(p["name"]) >= 2]
        if len(surname) == 1:
            p = surname[0]
            return PersonHit(
                alias=t,
                canonical=p["name"],
                open_id=p.get("open_id") or "",
                employee_no=p.get("employee_no") or "",
                score=0.88,
                source="org_surname",
            )

    # 5) 中文名后缀/内含子串（锦涛⊂杜锦涛）；仅唯一命中才自动展开
    if re.fullmatch(r"[\u4e00-\u9fff]{2,3}", t):
        end = [p for p in people if p["name"].endswith(t) and p["name"] != t]
        suf = [
            p
            for p in people
            if p["name"] != t and (p["name"].endswith(t) or (len(t) >= 2 and t in p["name"]))
        ]
        pool = end if len(end) == 1 else (suf if len(suf) == 1 else [])
        if len(pool) == 1:
            p = pool[0]
            return PersonHit(
                alias=t,
                canonical=p["name"],
                open_id=p.get("open_id") or "",
                employee_no=p.get("employee_no") or "",
                score=0.9,
                source="org_suffix",
            )

    return None


def resolve_people_in_text(
    text: str,
    *,
    session: Any = None,
    people: list[dict[str, str]] | None = None,
    use_org: bool = True,
) -> ResolveResult:
    tokens = candidate_tokens(text)
    pool: list[dict[str, str]] = []
    # session 优先（刚聊过的人）
    pool.extend(_session_people(session))
    if people is not None:
        pool.extend(people)
    elif use_org:
        pool.extend(_org_people())
    # 去重 by name
    by_name: dict[str, dict[str, str]] = {}
    for p in pool:
        n = p.get("name") or ""
        if n and n not in by_name:
            by_name[n] = p
    people_list = list(by_name.values())

    hits: list[PersonHit] = []
    unresolved: list[str] = []
    seen_alias: set[str] = set()
    for tok in tokens:
        if tok in seen_alias:
            continue
        hit = _match_token(tok, people_list)
        if hit and hit.canonical != hit.alias:
            hits.append(hit)
            seen_alias.add(tok)
        elif hit and hit.canonical == hit.alias:
            # 全名命中也记下来，方便下游
            hits.append(hit)
            seen_alias.add(tok)
        elif tok in alias_map() or (tok.isdigit() and len(tok) <= 4):
            unresolved.append(tok)

    expanded = expand_for_retrieval(text, hits)
    return ResolveResult(hits=hits, expanded_query=expanded, unresolved=unresolved)


def expand_for_retrieval(text: str, hits: list[PersonHit]) -> str:
    """保留用户原话，追加全名线索；对唯一简称做安全替换提示。"""
    q = (text or "").strip()
    if not hits:
        return q
    clues = []
    for h in hits:
        if h.alias == h.canonical:
            continue
        bit = f"{h.alias}→{h.canonical}"
        if h.employee_no:
            bit += f"（工号{h.employee_no}）"
        clues.append(bit)
    if not clues:
        return q
    # 正文里把简称旁注全名，便于 Ask FTS；不删除原简称
    annotated = q
    # 长 alias 优先，避免短串误伤
    for h in sorted(hits, key=lambda x: len(x.alias), reverse=True):
        if h.alias == h.canonical:
            continue
        if h.alias in annotated and h.canonical not in annotated:
            annotated = annotated.replace(h.alias, f"{h.canonical}（{h.alias}）")
    return (
        annotated
        + "\n\n【人名解析 · 检索用全名】\n"
        + "\n".join(f"- {c}" for c in clues)
    )


def lookup_by_open_id(open_id: str) -> dict[str, str] | None:
    """花名册 / 通讯录按 open_id 认人（不依赖 users 表是否绑过）。"""
    oid = (open_id or "").strip()
    if not oid:
        return None
    for p in _org_people():
        if str(p.get("open_id") or "").strip() == oid:
            return {
                "name": str(p.get("name") or "").strip(),
                "open_id": oid,
                "employee_no": str(p.get("employee_no") or "").strip(),
                "source": str(p.get("source") or "roster"),
                "job_title": str(p.get("job_title") or "").strip(),
                "teams": str(p.get("teams") or "").strip(),
            }
    # roster 行可能有 teams 列表；_org_people 已拍扁
    for row in load_roster().get("people") or []:
        if str(row.get("open_id") or "").strip() != oid:
            continue
        teams = row.get("teams") or []
        if isinstance(teams, list):
            team_s = ",".join(str(t).strip() for t in teams if str(t).strip())
        else:
            team_s = str(teams or "").strip()
        return {
            "name": str(row.get("name") or "").strip(),
            "open_id": oid,
            "employee_no": str(row.get("employee_no") or "").strip(),
            "source": "roster",
            "job_title": str(row.get("job_title") or "").strip(),
            "teams": team_s,
        }
    return None


def filter_known_people(names: list[str] | None, *, people: list[dict[str, str]] | None = None) -> list[str]:
    """只保留通讯录/花名册里的人；助手成文里的「已上线周报」等不算人。"""
    raw = [str(n or "").strip() for n in (names or []) if str(n or "").strip()]
    if not raw:
        return []
    pool = people if people is not None else _org_people()
    known = {str(p.get("name") or "").strip() for p in pool if str(p.get("name") or "").strip()}
    amap = alias_map()
    out: list[str] = []
    seen: set[str] = set()
    for s in raw:
        canon = s if s in known else (amap.get(s) or amap.get(s.lower()) or "")
        if canon in known and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def sanitize_session_people(session: Any) -> None:
    if session is None:
        return
    ents = filter_known_people(getattr(session, "active_entities", None) or [])
    session.active_entities = ents[:16]


def remember_hits(session: Any, hits: list[PersonHit]) -> None:
    if session is None:
        return
    sanitize_session_people(session)
    if not hits:
        return
    ents = list(getattr(session, "active_entities", None) or [])
    for h in hits:
        if h.canonical and h.canonical not in ents:
            ents.append(h.canonical)
    session.active_entities = ents[:16]
