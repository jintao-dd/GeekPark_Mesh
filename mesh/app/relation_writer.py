"""Relation Writing Module：把 Gate 锁定的 RelationObject 写成 Narrative。

Pipeline:
  Candidate → Decision → Evidence Gate → RelationObject → Writer → Verify → Published

写作模块只负责 title/body/details；label/teams/sources/evidence 等由上游代码锁死。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .relation_candidates import (
    _align_relation_from_evidence,
    _dedupe_relations_by_title,
    _facts_consistent,
    _suggested_teams,
    _teams_from_evidence,
)

# 写作模块不得触碰的字段
LOCKED_FIELDS = frozenset({
    "candidate_id",
    "candidate_title",
    "label",
    "relation_type",
    "decision_tier",
    "teams",
    "sources",
    "evidence",
    "item_ids",
    "weak",
    "provenance_ok",
    "needs_review",
    "status",
    "decision_reason",
    "relation_reason",
    "owner_team",
    "provenance",
    "decision",
})

WRITER_OUTPUT_KEYS = frozenset({"title", "body", "details"})

# 按卡注入的一行写作 hint（提炼自 Decision 定义；勿整表塞进 system prompt）
# 别名「同一条赛道…」经 normalize 后落到「同一赛道…」
LABEL_WRITE_HINTS: dict[str, str] = {
    "已联动": "只写 evidence 支撑的同一事项或连续动作；接触/提及不得写成合作或共同推进。",
    "同一件事，两个部门各知一半": "写清同一件事上两队各自掌握的事实；强调信息互补，不写成双方已协同执行。",
    "一方接触了，另一方正在接触": "分别写已接触与正在接触的事实进度；可点同事链，但不升级为已联动/已合作。",
    "同一赛道，各自在做": "分别写两队各自已发生动作；明确并行，禁止写成合作、共同推进或同一事链。",
    "同一公司，不同触点": "写同一公司上的不同触点/动作；并列事实，不并成一条合作叙事。",
    "两个部门各有判断": "并列两队各自判断/口径；不裁决对错，不写成已统一结论。",
    "采访对象也是客户": "写清采访侧与客户/商务侧各自已发生的事实；有明确事链才写衔接，否则保持并行。",
    "已公开报道，内部也在用": "一侧写公开报道事实，一侧按 evidence 原表述写内部使用、跟进等事实；不把报道写成内部合作。",
    "中英文站同周各自成稿": "分写中/英（或两站）同周各自成稿动作；保持并行，不写成联合稿或已联动。",
    "一方报道了，另一方在接触": "分写已报道与正在接触的事实；可点明共同对象或同事链，但不写成已联动、合作或商务关系已成立。",
    "两处记录待核对": "并列冲突点、不选边；body 可点「两边记录不一致需核对」，勿把标签整句当标题；细节各写各的记录原文要点。",
    "一方有需求，另一方尚未接触": "写清有需求侧已发生事实；另一侧仅写 evidence 明确记录的相关事实或「尚未接触」状态，不补充接触推断，不写「该谁去接」的路由建议。",
    "一方接触，另一方用得上": "只写接触/发现侧已发生事实；禁止「记录标注××用得上」等元话（承接方看虚线团队）。",
    "海外接触，国内可能承接": "海外动作须有 evidence；国内侧仅写已有事实，无国内证据就不要写「将承接/可承接」。",
    "海外新发现，国内尚未接触": "写海外新发现事实；国内侧仅写 evidence 明确记录的相关事实或「尚未接触」状态，不根据缺失证据推断国内未接触。",
    "外部在热聊，我们还没碰": "外部热度与内部未接触须都有可引用依据；不写成我们已在跟进或已联动。",
    "已排期，内容侧待安排": "写清已排期事实与内容侧待安排的现状；不写成内容已产出或已联动完成。",
}

# title/body 不得复读的标签腔（旗已展示类型）
_LABEL_LEAK_FRAGMENTS: tuple[str, ...] = (
    "各知一半",
    "不同触点",
    "已联动",
    "各自在做",
    "用得上",
    "尚未接触",
    "待核对",
    "两处记录待核对",
    "一方接触，另一方",
    "一方接触了，另一方",
    "一方有需求",
    "一方报道了",
    "海外接触，国内",
    "海外新发现",
    "外部在热聊",
    "采访对象也是客户",
    "已公开报道，内部也在用",
    "中英文站同周",
    "已排期，内容侧",
    "同一件事，两个部门",
    "同一公司，不同触点",
    "同一赛道，各自",
)

_BODY_TEMPLATE = re.compile(
    r"(这一合作|这一事项|这一关系).{0,12}(各掌握一部分|两边各|两侧各)"
    r"|各掌握一部分"
    r"|两边各掌握"
    r"|两侧各知一半"
    r"|两个部门各知一半"
    r"|一边.{0,16}一边"
    r"|分别掌握"
    r"|各写各的"
)

# 标题公式：乘号对照表 / 一边一边 / A侧B侧填空（整区同构会很难看）
_TITLE_CROSS = re.compile(r"[：:].{0,40}[×xX].{0,40}$|[：:].+[×xX].+")
_TITLE_PARALLEL_FILL = re.compile(
    r"一边.{0,20}一边|A侧|B侧|两侧各自|两队各自|左．+右"
)

_DETAIL_TEAM = re.compile(r"^(.+?)(?:记录|：|:)")
_TEAM_NAME_NOISE = re.compile(
    r"(商业化团队|编辑部|视频号团队|投资团队|硅谷\s*BD\s*团队|"
    r"Global Partnership\s*团队|总裁办|社群|英文站|外部媒体)"
)
_HINT_NO_LABEL_IN_COPY = (
    "类型只看 flag；title 禁止标签词与「A × B」对照表公式；"
    "body 只写圆点之外的增量，禁止复述 details。"
)


def label_write_hint(label: str) -> str:
    """本卡 label → 一行 Writer hint；未知标签返回空串。"""
    from .relation_decision_consistency import normalize_relation_label

    lab = normalize_relation_label((label or "").strip())
    base = LABEL_WRITE_HINTS.get(lab, "")
    if not base:
        return _HINT_NO_LABEL_IN_COPY
    return f"{base} {_HINT_NO_LABEL_IN_COPY}"


def title_has_label_leak(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    return any(frag in t for frag in _LABEL_LEAK_FRAGMENTS)


def body_is_template(body: str) -> bool:
    b = (body or "").strip()
    if not b:
        return False
    if _BODY_TEMPLATE.search(b):
        return True
    # 短句整句复读标签
    if len(b) < 40 and any(
        frag in b for frag in ("各知一半", "不同触点", "已联动", "各自在做")
    ):
        return True
    return False


def title_is_formula(title: str) -> bool:
    """标题是否落入整区同构公式（乘号对照表 / 一边一边等）。"""
    t = (title or "").strip()
    if not t:
        return False
    if "×" in t or _TITLE_CROSS.search(t):
        return True
    if _TITLE_PARALLEL_FILL.search(t):
        return True
    return False


def _detail_fact_core(detail: str, max_len: int = 28) -> str:
    ds = strip_route_meta_copy(str(detail or "").strip())
    m = _DETAIL_TEAM.match(ds)
    core = ds[m.end():].strip() if m else ds
    core = re.split(r"[，,；;。．]", core)[0].strip()
    return core[:max_len]


def _detail_full_core(detail: str) -> str:
    ds = strip_route_meta_copy(str(detail or "").strip())
    m = _DETAIL_TEAM.match(ds)
    return (ds[m.end():].strip() if m else ds)


def _subject_from_candidate(candidate_title: str) -> str:
    t = (candidate_title or "").strip()
    if not t:
        return ""
    t = re.split(r"[·•｜|]", t)[0].strip()
    t = re.split(r"[：:]", t)[0].strip()
    for frag in _LABEL_LEAK_FRAGMENTS:
        if frag in t:
            t = t.split(frag)[0].strip(" ·-—")
    return t[:20]


def _text_fingerprint(text: str) -> str:
    t = strip_route_meta_copy(text or "")
    t = _DETAIL_TEAM.sub("", t)
    t = _TEAM_NAME_NOISE.sub("", t)
    t = re.sub(r"[\s\d/年月日\.，,；;。．：:·\-×xX（）()【】\[\]]+", "", t)
    return t.lower()


def _char_bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _bigram_jaccard(a: str, b: str) -> float:
    aa, bb = _char_bigrams(a), _char_bigrams(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


_SYNC_MARKERS = (
    "却", "仍卡", "未对齐", "不对齐", "不同步", "差在", "缺口",
    "值得同步", "待对齐", "口径", "进度未", "两边进度", "尚未对齐",
    "卡在同一", "信息互补", "需要同步",
)
_GENERIC_TRIGRAMS = frozenset({
    "进行中", "沟通中", "接触中", "推进中", "已提报", "提报了",
    "商业化", "编辑部", "视频号", "投资团",
})


def _trigram_overlap(a: str, b: str) -> int:
    fa, fb = _text_fingerprint(a), _text_fingerprint(b)
    if len(fa) < 3 or len(fb) < 3:
        return 0
    ga = {fa[i : i + 3] for i in range(len(fa) - 2)}
    gb = {fb[i : i + 3] for i in range(len(fb) - 2)}
    return len(ga & gb)


def _contentful_trigram_hits(a: str, b: str) -> int:
    fa, fb = _text_fingerprint(a), _text_fingerprint(b)
    if len(fa) < 3 or len(fb) < 3:
        return 0
    ga = {fa[i : i + 3] for i in range(len(fa) - 2)}
    gb = {fb[i : i + 3] for i in range(len(fb) - 2)}
    return sum(1 for t in (ga & gb) if t not in _GENERIC_TRIGRAMS)


def _soft_overlap(a: str, b: str) -> bool:
    """半句与一条 detail 是否同指一件事（允许短 paraphrase）。"""
    if _contentful_trigram_hits(a, b) >= 1:
        return True
    if _trigram_overlap(a, b) >= 2:
        return True
    fa, fb = _text_fingerprint(a), _text_fingerprint(b)
    if len(fa) >= 4 and fa[:4] in fb:
        return True
    if len(fb) >= 4 and any(fb[i : i + 4] in fa for i in range(0, max(1, len(fb) - 3), 2)):
        return True
    return _bigram_jaccard(fa, fb) >= 0.28


def body_restates_details(body: str, details: list[str]) -> bool:
    """body 是否基本是 details 换皮复读（含「A…；B…」拼盘）。

    有跨队增量标记（却/未对齐/值得同步等）的短评可保留；纯两侧事实拼盘一律清。
    """
    b = (body or "").strip()
    if not b or not details:
        return False
    cores = [_detail_full_core(d) for d in details if str(d).strip()]
    cores = [c for c in cores if c]
    if not cores:
        return False

    bn = _text_fingerprint(b)
    if len(bn) < 6:
        return False

    has_sync = any(m in b for m in _SYNC_MARKERS)

    # 分号/句号拼盘：两半各贴一条 detail → 典型复读
    halves = [h.strip() for h in re.split(r"[；;]", b) if h.strip()]
    if len(halves) == 1:
        # 两句「。。」拼盘也算
        maybe = [h.strip() for h in re.split(r"[。．]", b) if h.strip()]
        if len(maybe) >= 2:
            halves = maybe
    if len(halves) >= 2 and len(cores) >= 2:
        used: set[int] = set()
        matched = 0
        for h in halves[:4]:
            for i, c in enumerate(cores):
                if i in used:
                    continue
                if _soft_overlap(h, c):
                    matched += 1
                    used.add(i)
                    break
        if matched >= 2:
            return True

    # 无增量标记时：两侧事实指纹都进了 body，或 body 高度覆盖 details
    hits = sum(1 for c in cores if _soft_overlap(b, c))
    if not has_sync and hits >= 2:
        return True

    det_fp = _text_fingerprint("。".join(cores))
    bg_b, bg_d = _char_bigrams(bn), _char_bigrams(det_fp)
    if bg_b and bg_d and not has_sync:
        inter = len(bg_b & bg_d)
        cov = inter / len(bg_b)
        if cov >= 0.70 and len(bg_b) >= 8:
            return True
    return False


def dedupe_near_details(details: list[str]) -> list[str]:
    """同卡近义 detail 只留一条（解决同队换皮重复）。"""
    kept: list[str] = []
    fps: list[str] = []
    for d in details or []:
        ds = strip_route_meta_copy(str(d).strip())
        if not ds:
            continue
        fp = _text_fingerprint(ds)
        drop = False
        for prev in fps:
            if not fp or not prev:
                continue
            if fp in prev or prev in fp or _bigram_jaccard(fp, prev) >= 0.72:
                drop = True
                break
        if drop:
            continue
        kept.append(ds)
        fps.append(fp)
    return kept


def title_from_details(
    details: list[str],
    *,
    candidate_title: str = "",
    evidence: list[dict] | None = None,
) -> str:
    """用 details/evidence 拼短钩子标题（无标签词、无 × 对照表）。"""
    cores = [_detail_fact_core(d) for d in (details or []) if str(d).strip()]
    cores = [c for c in cores if c]
    if len(cores) < 1 and evidence:
        for e in evidence:
            if not isinstance(e, dict):
                continue
            snip = (e.get("snippet") or e.get("quote") or "").strip()
            if snip:
                cores.append(re.split(r"[，,；;。．]", snip)[0].strip()[:28])
        cores = [c for c in cores if c]
    subj = _subject_from_candidate(candidate_title)
    if not cores:
        return (subj or candidate_title or "").strip()[:80]
    lead = cores[0]
    if subj:
        title = f"{subj}：{lead}"
    else:
        title = lead
    return title[:80]


def enforce_narrative_hygiene(
    *,
    title: str,
    body: str,
    details: list[str],
    candidate_title: str = "",
    evidence: list[dict] | None = None,
) -> tuple[str, str, list[str], dict[str, bool]]:
    """确定性收口：去公式标题、清套话/复读 body、去近重 details。

    返回 (title, body, details, flags)。
    """
    flags = {
        "title_rebuilt": False,
        "body_cleared_template": False,
        "body_cleared_restates": False,
        "details_deduped": False,
    }
    t = strip_route_meta_copy((title or "").strip())
    b = strip_route_meta_copy((body or "").strip())
    dets = [strip_route_meta_copy(str(d).strip()) for d in (details or []) if str(d).strip()]
    deduped = dedupe_near_details(dets)
    if len(deduped) < len(dets):
        flags["details_deduped"] = True
    dets = deduped

    need_title = (not t) or title_has_label_leak(t) or title_is_formula(t)
    if need_title:
        rebuilt = title_from_details(dets, candidate_title=candidate_title, evidence=evidence)
        if rebuilt:
            t = rebuilt
            flags["title_rebuilt"] = True
        elif title_has_label_leak(t) or title_is_formula(t):
            for frag in _LABEL_LEAK_FRAGMENTS:
                t = t.replace(frag, "")
            t = t.replace("×", "，")
            t = re.sub(r"[：:\s·×xX]{2,}", "：", t).strip(" ：:·-—")
            flags["title_rebuilt"] = True

    if title_is_formula(t):
        rebuilt = title_from_details(dets, candidate_title=candidate_title, evidence=evidence)
        if rebuilt and not title_is_formula(rebuilt):
            t = rebuilt
            flags["title_rebuilt"] = True

    if body_is_template(b) or (
        any(frag in b for frag in ("各知一半", "各掌握一部分", "两侧各知一半")) and len(b) < 56
    ):
        b = ""
        flags["body_cleared_template"] = True
    elif body_restates_details(b, dets):
        b = ""
        flags["body_cleared_restates"] = True

    return t, b, dets, flags


# 禁止写入读者文案的路由元叙述 / 标题尾巴
_META_ROUTE_COPY = re.compile(
    r"[，,]?\s*(?:记录|文中|材料|纪要)?(?:明确)?(?:标注|写明|注明)[^。；;]{0,40}(?:用得上|可承接|可用)[。．]?",
)
_META_ROUTE_COPY2 = re.compile(
    r"[，,]?\s*记录里?(?:写着|提到)[^。；;]{0,30}(?:用得上|可承接|可用)[。．]?",
)
# 「，投资团队用得上」「，投资团队可承接」「，CEO 可对接」——不含分号，避免吞掉前面事实句
_ROUTE_TAIL = re.compile(
    r"[，,]\s*(?:[^，,；;。．]{0,24}(?:用得上|可对接|可关注|可对齐|可承接|可用|采访池用得上))(?=[。．]|$)"
)
# 「；对编辑部选题、投资团队可用」「；国内编辑部选题可用得上」
_ROUTE_SEMI = re.compile(
    r"[；;]\s*(?:对|国内)[^；;。．]{0,48}(?:可承接|可用|用得上|可对接|可关注|可对齐)[。．]?"
)
# 「；编辑部用得上」「；投资团队可承接」——分号后队名+路由词（不要求「对/国内」）
_ROUTE_SEMI_TEAM = re.compile(
    r"[；;]\s*[^；;。．]{0,36}(?:用得上|可承接|可用|可对接|可关注|可对齐|采访池用得上)[。．]?"
)
# 句末孤立「…编辑部用得上」「…可用得上」（无逗号时也能剥）
_ROUTE_END = re.compile(
    r"(?:[，,；;]\s*)?[\w\u4e00-\u9fff（）()\s·\-]{0,24}(?:用得上|可用得上|可承接|可对接|可关注)[。．]?$"
)


def strip_route_meta_copy(text: str) -> str:
    """去掉路由元叙述：「…用得上 / 可对接 / 可承接 / 可用」及「；对xx可用」尾巴。"""
    t = (text or "").strip()
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _META_ROUTE_COPY.sub("", t)
        t = _META_ROUTE_COPY2.sub("", t)
        t = _ROUTE_TAIL.sub("", t)
        t = _ROUTE_SEMI.sub("", t)
        t = _ROUTE_SEMI_TEAM.sub("", t)
        t = _ROUTE_END.sub("", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"[；;]{2,}", "；", t)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 清完尾巴后可能残留逗号/分号，不要剥掉正常句号
    return re.sub(r"[，,；;]+$", "", t).strip()


def relation_object_from_gate(locked: dict) -> dict[str, Any]:
    """Gate 输出 → RelationObject（写作输入 + 锁字段载体）。"""
    reason = (locked.get("relation_reason") or locked.get("decision_reason") or "").strip()
    return {
        "candidate_id": locked.get("candidate_id"),
        "candidate_title": locked.get("candidate_title") or "",
        "label": locked.get("label"),
        "relation_type": locked.get("relation_type"),
        "decision_tier": locked.get("decision_tier"),
        "teams": list(locked.get("teams") or []),
        "sources": list(locked.get("sources") or []),
        "evidence": list(locked.get("evidence") or []),
        "item_ids": list(locked.get("item_ids") or []),
        "weak": locked.get("weak"),
        "provenance_ok": locked.get("provenance_ok"),
        "needs_review": locked.get("needs_review"),
        "status": locked.get("status"),
        "relation_reason": reason,
        "team_facts": list(locked.get("team_facts") or []),
    }


def to_writer_input(obj: dict) -> dict[str, Any]:
    """RelationObject → LLM 可见的精简写作输入。"""
    evidence = []
    for e in obj.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        evidence.append({
            "item_id": e.get("item_id"),
            "team": e.get("team"),
            "snippet": (e.get("snippet") or e.get("quote") or "")[:240],
            "source_label": e.get("source_label"),
        })
    solid_teams = [
        t for t in (obj.get("teams") or [])
        if str(t).strip() and not str(t).startswith("→")
    ]
    team_facts = []
    for tf in obj.get("team_facts") or []:
        if not isinstance(tf, dict):
            continue
        team_facts.append({
            "team": tf.get("team"),
            "snippets": [(s or "")[:200] for s in (tf.get("snippets") or [])[:3]],
        })
    label = obj.get("label") or ""
    return {
        "candidate_id": obj.get("candidate_id"),
        "label": label,
        "label_hint": label_write_hint(label),
        "teams": solid_teams,
        "evidence": evidence,
        "team_facts": team_facts,
        "relation_reason": obj.get("relation_reason") or "",
        "candidate_title": obj.get("candidate_title") or "",
    }


def _sanitize_writer_output(raw: dict) -> dict[str, Any]:
    """只保留 title/body/details；剥离任何试图覆盖锁字段的键。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k in WRITER_OUTPUT_KEYS:
        if k in raw:
            out[k] = raw[k]
    out["title"] = strip_route_meta_copy((out.get("title") or "").strip())
    out["body"] = strip_route_meta_copy((out.get("body") or "").strip())
    details = [
        strip_route_meta_copy(str(x).strip())
        for x in (out.get("details") or [])
        if str(x).strip()
    ]
    out["details"] = [d for d in details if d]
    return out


def _locked_snapshot(obj: dict) -> dict[str, Any]:
    return {
        "label": obj.get("label"),
        "relation_type": obj.get("relation_type"),
        "decision_tier": obj.get("decision_tier"),
        "teams": list(obj.get("teams") or []),
        "sources": list(obj.get("sources") or []),
        "evidence": list(obj.get("evidence") or []),
        "weak": obj.get("weak"),
        "item_ids": list(obj.get("item_ids") or []),
        "provenance_ok": obj.get("provenance_ok"),
    }


def _normalize_details_for_teams(rel: dict, snap: dict) -> list[str]:
    """尽量保证每个实线团队一条 detail，内容来自 evidence。"""
    evidence = list(snap.get("evidence") or [])
    allowed = set(_teams_from_evidence(evidence))
    details_in = list(rel.get("details") or [])
    by_team: dict[str, str] = {}

    for d in details_in:
        ds = str(d).strip()
        m = _DETAIL_TEAM.match(ds)
        if m:
            team = m.group(1).strip()
            if team in allowed and team not in by_team:
                by_team[team] = ds

    for e in evidence:
        team = (e.get("team") or "").strip()
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if team and team in allowed and team not in by_team and snip:
            by_team[team] = f"{team}：{snip}"

    ordered: list[str] = []
    for t in _teams_from_evidence(evidence):
        if t in by_team:
            ordered.append(by_team[t])
    return ordered[:8]


def _apply_hygiene_to_rel(rel: dict, obj: dict) -> dict:
    """merge 后强制 title/body/details 卫生。"""
    title, body, details, flags = enforce_narrative_hygiene(
        title=rel.get("title") or "",
        body=rel.get("body") or "",
        details=list(rel.get("details") or []),
        candidate_title=obj.get("candidate_title") or "",
        evidence=list(rel.get("evidence") or obj.get("evidence") or []),
    )
    rel["title"] = title
    rel["body"] = body
    rel["details"] = details
    if flags.get("title_rebuilt"):
        rel["_title_rebuilt_from_details"] = True
    if flags.get("body_cleared_template"):
        rel["_body_omitted_template"] = True
    if flags.get("body_cleared_restates"):
        rel["_body_omitted_restates_details"] = True
    if flags.get("details_deduped"):
        rel["_details_deduped"] = True
    return rel


def merge_writing(obj: dict, writing: dict) -> dict[str, Any]:
    """RelationObject + Writer 输出 → 带叙事的 relation（锁字段强制还原）。"""
    snap = _locked_snapshot(obj)
    w = _sanitize_writer_output(writing)
    rel = dict(obj)
    rel["title"] = w.get("title") or (obj.get("candidate_title") or "").strip()
    rel["body"] = w.get("body") or ""
    rel["details"] = w.get("details") or []
    rel = _align_relation_from_evidence(rel, suggested=_suggested_teams(snap))
    rel["details"] = _normalize_details_for_teams(rel, snap)
    # 强制锁字段
    rel["label"] = snap["label"]
    rel["relation_type"] = snap.get("relation_type")
    rel["decision_tier"] = snap.get("decision_tier")
    rel["teams"] = snap["teams"]
    rel["sources"] = snap["sources"]
    rel["evidence"] = snap["evidence"]
    rel["weak"] = snap["weak"]
    rel["item_ids"] = snap["item_ids"]
    rel["provenance_ok"] = snap["provenance_ok"]
    if not _facts_consistent(rel):
        rel["teams"] = snap["teams"]
        rel["sources"] = snap["sources"]
        rel["details"] = _align_relation_from_evidence(
            dict(rel), suggested=_suggested_teams(snap),
        ).get("details") or []
    rel = _apply_hygiene_to_rel(rel, obj)
    return rel


def call_writer_llm(objects: list[dict]) -> list[dict]:
    """调用 LLM 批量写作；返回含 candidate_id 的对齐结果。"""
    from . import llm

    if not objects:
        return []
    system = (
        llm.load_prompt("00_base_rules")
        + "\n\n"
        + llm.load_prompt("issue_relation_writer")
        + "\n\n"
        '输出严格 JSON：{"relation_writings":[...]}'
        " 每项仅含 candidate_id、title、body、details。"
    )
    payload = [to_writer_input(obj) for obj in objects]
    user = (
        f"【relation_objects】\n{json.dumps(payload, ensure_ascii=False)[:llm.budget(20000)]}\n\n"
        "对每个 candidate_id 写一条；遵守该卡 label_hint；不得修改 label/teams/evidence。"
        " title 写短钩子（禁止「A × B」对照表公式与标签词）；"
        " body 只写圆点之外的跨队增量（禁止把两条 detail 换皮拼成 A…；B…）；"
        " 无增量则 body 留空。"
    )
    out = llm.call_json_compliant(system, user, max_tokens=8000)
    rows = list(out.get("relation_writings") or out.get("relation_narratives") or [])
    by_id = {
        (r.get("candidate_id") or "").strip(): _sanitize_writer_output(r)
        for r in rows
        if isinstance(r, dict) and (r.get("candidate_id") or "").strip()
    }
    result: list[dict] = []
    for obj in objects:
        cid = (obj.get("candidate_id") or "").strip()
        w = dict(by_id.get(cid) or {})
        w["candidate_id"] = cid
        result.append(w)
    return result


def write_relations(
    relation_objects: list[dict],
    writings: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """RelationObject 列表 → 合并写作结果；返回 (relations, skipped)。"""
    objects = [relation_object_from_gate(o) for o in relation_objects if isinstance(o, dict)]
    if writings is None and objects:
        writings = call_writer_llm(objects)
    by_id = {
        (w.get("candidate_id") or "").strip(): w
        for w in (writings or [])
        if isinstance(w, dict)
    }

    out: list[dict] = []
    skipped: list[dict] = []
    for obj in objects:
        cid = (obj.get("candidate_id") or "").strip()
        w = by_id.get(cid) or {}
        rel = merge_writing(obj, w)
        title_ok = bool((rel.get("title") or "").strip())
        body_ok = bool((rel.get("body") or "").strip())
        details_ok = any(str(d).strip() for d in (rel.get("details") or []))
        # body 可空（与 label_hint / Verify 去重契约一致）；须有 title，且 body 或 details 其一
        if not title_ok or not (body_ok or details_ok):
            skipped.append({
                "candidate_id": cid,
                "reason": "missing_narrative_title_or_body",
            })
            continue
        out.append(rel)
    return _dedupe_relations_by_title(out), skipped


def fact_snapshot(obj: dict) -> dict[str, Any]:
    """供 pipeline 在 verify/filter 后还原锁字段。"""
    return _locked_snapshot(relation_object_from_gate(obj))
