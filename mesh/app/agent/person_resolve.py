"""Person resolve — 简称 / 工号 / 会话提及 → 通讯录全名。

这是 Company Understanding 的人名词典层，不是周报向量 Memory。
用于检索前扩展：锦涛→杜锦涛，思琪→赵思琪，49→彭康林。
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("uvicorn.error")

# 人工种子别名（工号/花名）；通讯录命中后会自动覆盖增强
SEED_ALIASES: dict[str, str] = {
    "49": "彭康林",
    "peng49": "彭康林",
    "锦涛": "杜锦涛",
    "思琪": "赵思琪",
    "山山": "张山山",
    "康林": "彭康林",
    "晓龙": "闫晓龙",
}

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
    }
)

_CJK_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,4}")
_CODE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]*\d{1,6}|\d{1,6})(?![A-Za-z0-9_])")


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
    for ent in getattr(session, "active_entities", None) or []:
        name = str(ent or "").strip()
        if name and not any(x["name"] == name for x in out):
            out.append({"name": name, "open_id": "", "employee_no": "", "source": "session"})
    return out


def _org_people() -> list[dict[str, str]]:
    try:
        from .feishu_hands import org_directory

        _depts, people = org_directory.load_directory()
        out = []
        for u in people or []:
            if not isinstance(u, dict):
                continue
            name = str(u.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "open_id": str(u.get("open_id") or "").strip(),
                    "employee_no": str(u.get("employee_no") or "").strip(),
                    "source": "org",
                }
            )
        return out
    except Exception as e:
        log.info("person_resolve org unavailable: %s", e)
        return []


def candidate_tokens(text: str) -> list[str]:
    """抽出可能人名/工号；中文按 2～4 字滑窗，避免「锦涛最近」被吞成 4 字无效串。"""
    q = text or ""
    found: list[str] = []

    def _add(tok: str) -> None:
        if not tok or tok in _STOP or tok in found:
            return
        found.append(tok)

    for m in re.finditer(r"[\u4e00-\u9fff]+", q):
        run = m.group(0)
        n = len(run)
        if 2 <= n <= 4:
            _add(run)
        for L in (2, 3, 4):
            if n < L:
                continue
            for i in range(0, n - L + 1):
                _add(run[i : i + L])
    for m in _CODE_TOKEN.finditer(q):
        _add(m.group(1))
    return found


def _match_token(token: str, people: list[dict[str, str]]) -> PersonHit | None:
    t = (token or "").strip()
    if not t:
        return None
    tl = t.lower()

    # 1) seed alias
    seed = SEED_ALIASES.get(t) or SEED_ALIASES.get(tl)
    if seed:
        # 若通讯录有此人，补 open_id
        for p in people:
            if p["name"] == seed:
                return PersonHit(
                    alias=t,
                    canonical=seed,
                    open_id=p.get("open_id") or "",
                    employee_no=p.get("employee_no") or "",
                    score=0.99,
                    source="seed",
                )
        return PersonHit(alias=t, canonical=seed, score=0.95, source="seed")

    # 2) exact full name
    exact = [p for p in people if p["name"] == t]
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

    # 4) 中文名后缀/内含子串（锦涛⊂杜锦涛）；仅唯一命中才自动展开
    if re.fullmatch(r"[\u4e00-\u9fff]{2,3}", t):
        suf = [
            p
            for p in people
            if p["name"].endswith(t) or (len(t) >= 2 and t in p["name"] and p["name"] != t)
        ]
        # 优先 endswith
        end = [p for p in people if p["name"].endswith(t) and p["name"] != t]
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
        elif tok in SEED_ALIASES or (tok.isdigit() and len(tok) <= 4):
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


def remember_hits(session: Any, hits: list[PersonHit]) -> None:
    if session is None or not hits:
        return
    ents = list(getattr(session, "active_entities", None) or [])
    for h in hits:
        if h.canonical and h.canonical not in ents:
            ents.append(h.canonical)
    session.active_entities = ents[:16]
