"""Phase 3 Verify v2：周报 relation narrative 必须 grounded 到 evidence[]。"""
from __future__ import annotations

import re

from . import qa_structured

_STOP = frozenset({
    "团队", "记录", "接触", "跟进", "相关", "本期", "已经", "进行", "方面", "目前",
    "哪些", "什么", "如何", "可以", "我们", "他们", "这个", "那个", "以及", "同时",
})
_DATE_RE = re.compile(r"20\d{2}[-/年]\d{1,2}|(?:\d{1,2}月\d{1,2}日)")

_WEAK_LABEL_HINTS = (
    "一方接触", "外部在热聊", "海外", "尚未接触", "用得上", "值得关注", "待核对",
)


def editorial_weak(rel: dict) -> bool:
    """编辑语义虚线卡（LLM weak 或 label 暗示），不由 verify 覆盖。"""
    if rel.get("weak"):
        return True
    label = (rel.get("label") or "").strip()
    return any(h in label for h in _WEAK_LABEL_HINTS)


def _key_tokens(text: str) -> list[str]:
    out: list[str] = []
    for t in re.findall(r"[\u4e00-\u9fff]{2,10}", text or ""):
        if t in _STOP:
            continue
        out.append(t)
    return out


def evidence_blob(rel: dict) -> str:
    parts = [str(rel.get("title") or "")]
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        for k in ("snippet", "quote", "team", "source_label", "pointer"):
            v = e.get(k)
            if v:
                parts.append(str(v))
    for t in rel.get("teams") or []:
        parts.append(str(t))
    for s in rel.get("sources") or []:
        parts.append(str(s))
    return "\n".join(parts)


def evidence_teams(rel: dict) -> set[str]:
    teams = set(rel.get("teams") or [])
    for e in rel.get("evidence") or []:
        if isinstance(e, dict) and e.get("team"):
            teams.add(str(e["team"]))
    return teams


def _unsupported_team_claim(line: str, allowed: set[str]) -> bool:
    for t in qa_structured.find_teams_in_question(line):
        if t not in allowed:
            return True
    return False


def _unsupported_date(line: str, blob: str) -> bool:
    for d in _DATE_RE.findall(line):
        if d not in blob:
            return True
    return False


def line_grounded(line: str, rel: dict, *, min_ratio: float = 0.34) -> bool:
    """一行 narrative 是否可由 evidence 支持（允许 paraphrase，禁止新事实）。"""
    s = (line or "").strip()
    if not s or len(s) < 4:
        return True
    blob = evidence_blob(rel)
    if _unsupported_team_claim(s, evidence_teams(rel)):
        return False
    if _unsupported_date(s, blob):
        return False
    tokens = _key_tokens(s)
    if not tokens:
        return True
    corpus = blob + "\n" + str(rel.get("title") or "")
    hits = sum(1 for t in tokens if t in corpus)
    return hits / len(tokens) >= min_ratio


def _details_from_evidence(rel: dict, limit: int = 6) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        team = (e.get("team") or "").strip()
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if not snip:
            continue
        line = f"{team}记录：{snip}" if team else snip
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


_TEAM_RECORD_PREFIX = re.compile(
    r"^[\w\u4e00-\u9fff（）()\s·\-]{1,40}(?:团队)?记录[：:]\s*"
)


def _strip_team_record_prefix(line: str) -> str:
    s = (line or "").strip()
    return _TEAM_RECORD_PREFIX.sub("", s).strip() or s


def _first_evidence_snippet(rel: dict, *, max_len: int = 120) -> str:
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if snip:
            return snip[:max_len]
    return ""


def body_redundant_with_details(body: str, details: list | None) -> bool:
    """body 与 details 实质重复（含 join 回填的旧数据）。"""
    b = (body or "").strip()
    if not b:
        return False
    dets = [(d or "").strip() for d in (details or []) if str(d).strip()]
    if not dets:
        return False
    if any(b == d for d in dets):
        return True
    joined = "；".join(dets[:3])
    if b == joined or b == joined[:500]:
        return True
    b_core = _strip_team_record_prefix(b)
    if b_core and any(b_core == _strip_team_record_prefix(d) for d in dets):
        return True
    return False


def verify_relation_narrative(rel: dict) -> dict:
    """Verify 只写 needs_review；虚线团队徽章（→）由 normalize 负责，不在整张卡上。"""
    from .owner_guard import normalize_relation_team_badges

    rel = normalize_relation_team_badges(dict(rel), suggest_extra_solid=editorial_weak(rel))
    evidence = list(rel.get("evidence") or [])

    if not evidence:
        rel["needs_review"] = True
        rel["status"] = "needs_review"
        if not rel.get("details"):
            rel["details"] = []
        return rel

    kept_details: list[str] = []
    for d in rel.get("details") or []:
        ds = str(d).strip()
        if ds and line_grounded(ds, rel):
            kept_details.append(d)
    if not kept_details:
        kept_details = _details_from_evidence(rel)
    rel["details"] = kept_details[:8]

    body = (rel.get("body") or "").strip()
    needs_review = bool(not body or (body and not line_grounded(body, rel, min_ratio=0.28)))
    if needs_review:
        # 禁止把 details 整段拼进 body（会造成页面 body/details 双显）。
        # 优先用 evidence 纯 snippet 写短 body；若仍与 detail 重复则清空 body，靠 details 展示。
        short = _first_evidence_snippet(rel, max_len=120)
        if short and not body_redundant_with_details(short, kept_details):
            rel["body"] = short
            rel["needs_review"] = False
            rel["status"] = "confirmed"
            rel["_body_from_evidence"] = True
        elif kept_details:
            rel["body"] = ""
            rel["needs_review"] = False
            rel["status"] = "confirmed"
            rel["_body_from_evidence"] = True
            rel["_body_omitted_dup"] = True
        else:
            rel["needs_review"] = True
            rel["status"] = "needs_review"
    else:
        # Writer body 过了论证，但仍可能与 detail 同文 → 去掉 body 重复
        if body_redundant_with_details(body, kept_details):
            rel["body"] = ""
            rel["_body_omitted_dup"] = True
        rel["needs_review"] = False
        rel["status"] = "confirmed"
    return rel


def verify_relations_narratives(relations: list[dict]) -> list[dict]:
    return [verify_relation_narrative(r) for r in (relations or [])]
