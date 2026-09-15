"""Phase 3 Verify v2：周报 relation narrative 必须 grounded 到 evidence[]。

质量优先（2026-09）：
  - details：严字面 grounding；缺队时按 evidence 回填
  - body：关系总结允许 paraphrase；只拦新团队/新日期、抄 detail、无锚点胡写
  - 禁止把合格总结用 token 重合率误杀后塞 snippet
"""
from __future__ import annotations

import re

from . import qa_structured

_STOP = frozenset({
    "团队", "记录", "接触", "跟进", "相关", "本期", "已经", "进行", "方面", "目前",
    "哪些", "什么", "如何", "可以", "我们", "他们", "这个", "那个", "以及", "同时",
})
# 总结常用关系元词：不算「新事实」，也不参与 body 重合率惩罚
_BODY_META_STOP = frozenset({
    "两侧", "两队", "各自", "触点", "不同", "并行", "各知", "一半", "同一", "关系",
    "分别", "掌握", "进度", "互补", "信息", "总结", "事项", "话题", "赛道", "对象",
    "商业化", "编辑部", "视频号", "投资", "社群", "硅谷", "英文站",
    "Global", "Partnership",
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
    """抽用于重合检查的短 token（2–4 字 + 拉丁专名）。

    过长贪婪块（旧 {2,10}）会把整句捏成一个「幽灵 token」，
    paraphrase 总结永远对不上，而「编辑部：胡写」又会因队名命中而假通过。
    """
    out: list[str] = []
    for t in re.findall(r"[\u4e00-\u9fff]{2,4}", text or ""):
        if t in _STOP:
            continue
        out.append(t)
    for m in re.findall(r"[A-Za-z][A-Za-z0-9][A-Za-z0-9+.\-]{1,}", text or ""):
        out.append(m)
    return out


def evidence_blob(rel: dict) -> str:
    parts = [str(rel.get("title") or "")]
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        for k in ("snippet", "quote", "source_label", "pointer"):
            v = e.get(k)
            if v:
                parts.append(str(v))
    # 故意不把 teams 拼进 grounding 语料：否则「编辑部：胡写」会因队名命中而假通过
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


def body_summary_grounded(body: str, rel: dict) -> bool:
    """关系总结 grounding：允许 paraphrase / 关系元词，要求有 evidence 锚点。

    与 details 的高重合门槛分开——总结本来就不该是 evidence 摘抄。
    """
    s = (body or "").strip()
    if not s:
        return False
    blob = evidence_blob(rel)
    if _unsupported_team_claim(s, evidence_teams(rel)):
        return False
    if _unsupported_date(s, blob):
        return False

    # 标题主实体或 evidence 专名至少命中一个
    title = (rel.get("title") or "").strip()
    primary = title
    for sep in (" · ", " / ", "：", ":"):
        if sep in primary:
            primary = primary.split(sep, 1)[0].strip()
            break
    anchor_ok = False
    if primary and len(primary) >= 2 and primary in s:
        anchor_ok = True
    if not anchor_ok:
        for e in rel.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            snip = (e.get("snippet") or e.get("quote") or "")
            for tok in _key_tokens(snip):
                if tok in _BODY_META_STOP or len(tok) < 2:
                    continue
                if tok in s:
                    anchor_ok = True
                    break
            if anchor_ok:
                break
            for m in re.findall(r"[A-Za-z][A-Za-z0-9][A-Za-z0-9+.\-]{1,}", snip):
                if m.lower() in s.lower():
                    anchor_ok = True
                    break
            if anchor_ok:
                break
            # 短实体：evidence 与 body 共享连续 2 字（如「甲相关」↔「跟甲」里的不足，用 snippet 内 2-gram）
            cn = "".join(ch for ch in snip if "\u4e00" <= ch <= "\u9fff")
            for i in range(len(cn) - 1):
                bi = cn[i : i + 2]
                if bi in _BODY_META_STOP or bi in _STOP:
                    continue
                if bi in s:
                    anchor_ok = True
                    break
            if anchor_ok:
                break
    if not anchor_ok:
        return False

    # 去掉关系元词后再看重合；门槛显著低于 detail
    tokens = [t for t in _key_tokens(s) if t not in _BODY_META_STOP]
    if not tokens:
        return True
    corpus = blob + "\n" + title
    hits = sum(1 for t in tokens if t in corpus)
    return hits / len(tokens) >= 0.15


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
        line = f"{team}：{snip}" if team else snip
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
_TEAM_COLON_PREFIX = re.compile(
    r"^[\w\u4e00-\u9fffA-Za-z0-9（）()\s·\-]{1,40}：\s*"
)


def _strip_team_record_prefix(line: str) -> str:
    s = (line or "").strip()
    s = _TEAM_RECORD_PREFIX.sub("", s).strip() or s
    s = _TEAM_COLON_PREFIX.sub("", s).strip() or s
    return s


def _detail_team_name(line: str) -> str | None:
    s = (line or "").strip()
    m = re.match(r"^([\w\u4e00-\u9fffA-Za-z0-9（）()\s·\-]{1,40}?)(?:记录)?[：:]", s)
    if m:
        return m.group(1).strip()
    return None


def _backfill_details_by_team(kept: list[str], rel: dict) -> list[str]:
    """部分 detail grounding 失败时，按实线队从 evidence 补齐（避免单侧糊墙）。"""
    from .relation_candidates import _teams_from_evidence

    solid = [t for t in _teams_from_evidence(list(rel.get("evidence") or [])) if t]
    if not solid:
        return kept[:8]
    have: set[str] = set()
    for d in kept:
        tn = _detail_team_name(str(d))
        if tn:
            have.add(tn)
    by_team: dict[str, str] = {}
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        team = (e.get("team") or "").strip()
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if team and snip and team not in by_team:
            by_team[team] = f"{team}：{snip}"
    out = list(kept)
    for t in solid:
        if t not in have and t in by_team:
            out.append(by_team[t])
            have.add(t)
    return out[:8]


def _first_evidence_snippet(rel: dict, *, max_len: int = 120) -> str:
    for e in rel.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if snip:
            return snip[:max_len]
    return ""


def body_redundant_with_details(body: str, details: list | None) -> bool:
    """body 与 details 实质重复（全等、join 回填、或互为包含的近义摘抄）。"""
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
    for d in dets:
        d_core = _strip_team_record_prefix(d)
        if b_core and d_core and b_core == d_core:
            return True
        # 近义摘抄：一方包含另一方且较短侧足够长（避免误伤短实体名）
        if b_core and d_core and len(b_core) >= 16 and len(d_core) >= 16:
            if b_core in d_core or d_core in b_core:
                return True
    return False


def verify_relation_narrative(rel: dict) -> dict:
    """Verify 只写 needs_review；虚线团队徽章（→）由 normalize 负责，不在整张卡上。"""
    from .owner_guard import normalize_relation_team_badges
    from .relation_writer import strip_route_meta_copy

    rel = normalize_relation_team_badges(dict(rel), suggest_extra_solid=editorial_weak(rel))
    evidence = list(rel.get("evidence") or [])

    if not evidence:
        rel["needs_review"] = True
        rel["status"] = "needs_review"
        if not rel.get("details"):
            rel["details"] = []
        return rel

    # 先剥路由尾巴，再做 grounded / 去重
    if rel.get("body"):
        rel["body"] = strip_route_meta_copy(str(rel.get("body") or ""))
    if rel.get("details"):
        rel["details"] = [
            strip_route_meta_copy(str(d)) for d in (rel.get("details") or []) if str(d).strip()
        ]

    kept_details: list[str] = []
    for d in rel.get("details") or []:
        ds = str(d).strip()
        if ds and line_grounded(ds, rel):
            kept_details.append(d)
    if not kept_details:
        kept_details = _details_from_evidence(rel)
        kept_details = [strip_route_meta_copy(x) for x in kept_details if x]
    else:
        kept_details = _backfill_details_by_team(kept_details, rel)
        kept_details = [strip_route_meta_copy(x) for x in kept_details if x]
    rel["details"] = kept_details[:8]

    body = (rel.get("body") or "").strip()
    # 与 details 重复的「总结」直接清空（含近义摘抄）
    if body and body_redundant_with_details(body, kept_details):
        rel["body"] = ""
        rel["_body_omitted_dup"] = True
        body = ""

    if not body:
        # 无 body：读者页不可见（见 relation_display._card_complete）；不塞 snippet
        if kept_details:
            rel["needs_review"] = False
            rel["status"] = "confirmed"
            rel["_body_omitted_empty"] = True
        else:
            rel["needs_review"] = True
            rel["status"] = "needs_review"
        return rel

    if body_summary_grounded(body, rel):
        rel["needs_review"] = False
        rel["status"] = "confirmed"
        rel.pop("_body_omitted_ungrounded", None)
        return rel

    # 真·未 grounded（新事实/无锚点）：清空，不塞 snippet
    rel["body"] = ""
    rel["needs_review"] = False if kept_details else True
    rel["status"] = "confirmed" if kept_details else "needs_review"
    rel["_body_from_evidence"] = True
    rel["_body_omitted_ungrounded"] = True
    return rel


def verify_relations_narratives(relations: list[dict]) -> list[dict]:
    return [verify_relation_narrative(r) for r in (relations or [])]
