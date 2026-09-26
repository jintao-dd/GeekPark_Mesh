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

# body 收尾套话（半公式）：允许句中叙述，禁止整句甩这类收尾
_BODY_CLOSING_CLICHE = re.compile(
    r'[，,；;]?\s*(?:'
    r'两边进度可对照|两边掌握的部分可对上|两边可对上|两边各握一段|'
    r'两边对进度的记录可以对上|两条触点指向同一家公司|两边各知一半|'
    r'两边掌握的进度可对照'
    r')[。．！？!?]*$'
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


def body_has_label_leak(body: str) -> bool:
    """body 是否复读标签原词（与 title 同级禁止）。"""
    b = (body or "").strip()
    if not b:
        return False
    return any(frag in b for frag in _LABEL_LEAK_FRAGMENTS)


def body_has_closing_cliche(body: str) -> bool:
    b = (body or "").strip()
    if not b:
        return False
    return bool(_BODY_CLOSING_CLICHE.search(b))


def strip_body_closing_cliche(body: str) -> str:
    b = (body or "").strip()
    if not b:
        return ""
    return _BODY_CLOSING_CLICHE.sub("", b).strip(" ，,；;。．")


def body_is_template(body: str) -> bool:
    b = (body or "").strip()
    if not b:
        return False
    if _BODY_TEMPLATE.search(b):
        return True
    if body_has_label_leak(b):
        return True
    if body_has_closing_cliche(b) and len(strip_body_closing_cliche(b)) < 12:
        return True
    # 短句整句复读标签
    if len(b) < 40 and any(
        frag in b for frag in ("各知一半", "不同触点", "已联动", "各自在做")
    ):
        return True
    return False


def title_is_formula(title: str) -> bool:
    """标题是否落入整区同构公式（乘号对照表 / 一边一边 / 主体：A，B 双列）。"""
    t = (title or "").strip()
    if not t:
        return False
    if "×" in t or _TITLE_CROSS.search(t):
        return True
    if _TITLE_PARALLEL_FILL.search(t):
        return True
    # 「主体：左事实，右事实」双列对照（两段都够长才算公式）
    if "：" in t or ":" in t:
        after = re.split(r"[：:]", t, maxsplit=1)[-1].strip()
        parts = re.split(r"[，,]", after, maxsplit=1)
        if len(parts) == 2:
            left, right = parts[0].strip(), parts[1].strip()
            if len(left) >= 6 and len(right) >= 6:
                teamish = ("编辑部", "商业化", "视频号", "两边", "两队", "一侧", "另一")
                if any(x in after for x in teamish) or (len(left) >= 8 and len(right) >= 8):
                    return True
    return False


# 标题动作/叙事锚点：有这些就不是「纯人名」
_TITLE_ACTION_MARKERS = re.compile(
    r"沟通|采访|接触|在谈|推进|合作|签约|专访|建联|约稿|催稿|催初|落地|报名|对接|跟进|"
    r"直播|年框|合同|选题|活动|实测|评测|转写|workshop|Meetup|Demo|"
    r"任|做|谈|推|签|访|聊|约|见|开|发|报|写|测|排|投"
)
# Latin 人名：Arvin Sun / Brad Yuan / Kein Tung（含可选中间名）
_LATIN_PERSON_TITLE = re.compile(
    r"^[A-Z][a-z]+(?:\s+[A-Z][a-z'.\-]+){1,3}$"
)


def title_is_bare_person_name(title: str) -> bool:
    """标题是否只是一个人名（无动作/事实钩子）。

    典型坏例：``Arvin Sun``、``Brad Yuan``。
    不拦公司短名（xAI / Notta.ai）与带叙事的人名标题（陈宇森任…总经理）。
    """
    t = (title or "").strip()
    if not t or len(t) > 28:
        return False
    if _TITLE_ACTION_MARKERS.search(t):
        return False
    # 有中文标点/对仗结构 → 已是叙事钩子
    if any(ch in t for ch in "：:，,。．；;·×|/／、"):
        return False
    if _LATIN_PERSON_TITLE.match(t):
        return True
    return False


def _clip_at_boundary(text: str, max_len: int) -> str:
    """限长时尽量落在标点/空白，避免半截字硬砍。"""
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    chunk = s[:max_len]
    for sep in ("，", ",", "；", ";", "、", " ", "·"):
        i = chunk.rfind(sep)
        if i >= max(8, max_len // 3):
            return chunk[:i].strip()
    return chunk.rstrip("，,；;、· ")


def _detail_fact_core(detail: str, max_len: int = 28) -> str:
    ds = strip_route_meta_copy(str(detail or "").strip())
    m = _DETAIL_TEAM.match(ds)
    core = ds[m.end():].strip() if m else ds
    core = re.split(r"[，,；;。．]", core)[0].strip()
    return _clip_at_boundary(core, max_len)


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
                cores.append(
                    _clip_at_boundary(re.split(r"[，,；;。．]", snip)[0].strip(), 28)
                )
        cores = [c for c in cores if c]
    subj = _subject_from_candidate(candidate_title)
    if not cores:
        return _clip_at_boundary((subj or candidate_title or "").strip(), 80)
    lead = cores[0]
    if subj:
        title = f"{subj}：{lead}"
    else:
        title = lead
    return _clip_at_boundary(title, 80)


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
        "title_bare_person": False,
        "body_cleared_template": False,
        "body_cleared_restates": False,
        "body_cleared_cliche": False,
        "body_cleared_label_leak": False,
        "details_deduped": False,
    }
    t = strip_route_meta_copy((title or "").strip())
    b = strip_route_meta_copy((body or "").strip())
    dets = [strip_route_meta_copy(str(d).strip()) for d in (details or []) if str(d).strip()]
    deduped = dedupe_near_details(dets)
    if len(deduped) < len(dets):
        flags["details_deduped"] = True
    dets = deduped

    need_title = (
        (not t)
        or title_has_label_leak(t)
        or title_is_formula(t)
        or title_is_bare_person_name(t)
    )
    if need_title:
        rebuilt = title_from_details(dets, candidate_title=candidate_title, evidence=evidence)
        if rebuilt and not title_is_bare_person_name(rebuilt):
            t = rebuilt
            flags["title_rebuilt"] = True
        elif title_has_label_leak(t) or title_is_formula(t):
            for frag in _LABEL_LEAK_FRAGMENTS:
                t = t.replace(frag, "")
            t = t.replace("×", "，")
            t = re.sub(r"[：:\s·×xX]{2,}", "：", t).strip(" ：:·-—")
            flags["title_rebuilt"] = True

    if title_is_formula(t) or title_is_bare_person_name(t):
        rebuilt = title_from_details(dets, candidate_title=candidate_title, evidence=evidence)
        if rebuilt and not title_is_formula(rebuilt) and not title_is_bare_person_name(rebuilt):
            t = rebuilt
            flags["title_rebuilt"] = True
    if title_is_bare_person_name(t):
        flags["title_bare_person"] = True

    if body_has_label_leak(b):
        b = ""
        flags["body_cleared_label_leak"] = True
        flags["body_cleared_template"] = True
    elif body_is_template(b) or (
        any(frag in b for frag in ("各知一半", "各掌握一部分", "两侧各知一半")) and len(b) < 56
    ):
        b = ""
        flags["body_cleared_template"] = True
    elif body_has_closing_cliche(b):
        stripped = strip_body_closing_cliche(b)
        if stripped and len(stripped) >= 12:
            b = stripped
            flags["body_cleared_cliche"] = True
        else:
            b = ""
            flags["body_cleared_cliche"] = True
            flags["body_cleared_template"] = True
    # 不再因「复读 details」清空 body；也不用截字拼 body——空 body 留给字段重写或最终留空成卡。

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


def narrative_violation_codes(
    title: str,
    body: str,
    details: list[str],
) -> list[str]:
    """硬闸同款检测，只返回原因码（不改写）。"""
    codes: list[str] = []
    t = (title or "").strip()
    b = (body or "").strip()
    dets = [str(d).strip() for d in (details or []) if str(d).strip()]
    if not t:
        codes.append("title_empty")
    if title_has_label_leak(t):
        codes.append("title_label_leak")
    if title_is_formula(t):
        codes.append("title_formula")
    if title_is_bare_person_name(t):
        codes.append("title_bare_person")
    if body_has_label_leak(b):
        codes.append("body_label_leak")
    elif body_has_closing_cliche(b):
        codes.append("body_closing_cliche")
    elif body_is_template(b) or (
        b
        and any(frag in b for frag in ("各知一半", "各掌握一部分", "两侧各知一半"))
        and len(b) < 56
    ):
        codes.append("body_template")
    elif not b and dets:
        # 有圆点无总结 → 优先让模型补一句（不成卡不再因此否决）
        codes.append("body_empty")
    return codes


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
    if flags.get("body_cleared_cliche") or flags.get("body_cleared_label_leak"):
        rel["_body_omitted_template"] = True
    if flags.get("body_cleared_restates"):
        rel["_body_omitted_restates_details"] = True
    if flags.get("details_deduped"):
        rel["_details_deduped"] = True
    return rel


def merge_writing(
    obj: dict,
    writing: dict,
    *,
    apply_hygiene: bool = True,
) -> dict[str, Any]:
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
    if apply_hygiene:
        rel = _apply_hygiene_to_rel(rel, obj)
    else:
        # 仍做 details 近重，便于重写时带锁定圆点
        dets = dedupe_near_details(
            [strip_route_meta_copy(str(d).strip()) for d in (rel.get("details") or []) if str(d).strip()]
        )
        rel["details"] = dets
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
        " body 按本卡 label_hint + evidence 写一句跨队关系总结（可 paraphrase 两侧事实，禁止空壳套话）；"
        " 有 details 时尽量写出 body；写不出可留空——系统不会用截字拼 body。"
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


_FIX_CODE_HINTS = {
    "title_formula": "title 禁止再写成「主体：A × B」或一边一边对照表；改短钩子。",
    "title_bare_person": "title 禁止纯人名（如 Arvin Sun）；必须带动作/事实钩子（沟通/投资/专访等）。",
    "title_label_leak": "title 禁止标签词（各知一半/不同触点/已联动等）。",
    "title_empty": "title 不能为空；用主体+最关键事实写短钩子。",
    "body_template": "body 禁止套话（各掌握一部分/这一合作等）；请改写为一句跨队事实总结。",
    "body_empty": "请按本卡 label + label_hint + evidence 写一句跨队总结；details_locked 仅作参考勿整段抄写；写不出可留空。",
    "body_restates": "body 按 label+evidence 概括两侧，勿空壳套话；勿整段抄 details_locked。",
    "body_label_leak": "body 禁止标签原词（各知一半/不同触点等）；改写成事实总结。",
    "body_closing_cliche": "body 禁止「两边可对照/可对上/各握一段」等收尾套话；写清卡点或进度差即可。",
    "body_ungrounded": "上一句总结无法由 evidence 证明，已清空；请严格按 evidence 重写一句，勿引入新事实。",
}


def call_writer_field_rewrite(tasks: list[dict]) -> dict[str, dict]:
    """违规字段局部重写（一轮）。返回 candidate_id → {title?, body?}。"""
    from . import llm

    if not tasks:
        return {}
    system = (
        llm.load_prompt("00_base_rules")
        + "\n\n"
        + llm.load_prompt("issue_relation_writer")
        + "\n\n"
        "你正在做**字段级修补**：只改 fix_fields 列出的字段。"
        '输出严格 JSON：{"relation_fixes":[{"candidate_id":"...","title":"...","body":"..."}]}'
        " 未要求修改的字段不要输出；details 禁止输出。"
    )
    slim = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        codes = list(t.get("fix_codes") or [])
        fields = list(t.get("fix_fields") or [])
        slim.append({
            "candidate_id": t.get("candidate_id"),
            "label": t.get("label"),
            "label_hint": t.get("label_hint"),
            "evidence": t.get("evidence") or [],
            "details_locked": t.get("details_locked") or [],
            "fix_codes": codes,
            "fix_fields": fields,
            "bad_title": t.get("bad_title") or "",
            "bad_body": t.get("bad_body") or "",
            "fix_hints": [_FIX_CODE_HINTS.get(c, c) for c in codes],
        })
    if not slim:
        return {}
    user = (
        f"【relation_fixes】\n{json.dumps(slim, ensure_ascii=False)[:llm.budget(16000)]}\n\n"
        "对每条：只重写 fix_fields；body 以 label_hint + evidence 为准写总结；"
        "details_locked 只防空壳/抄写，不是拼接原料；写不出 body 可输出空字符串；禁止 A × B 标题公式。"
    )
    try:
        out = llm.call_json_compliant(system, user, max_tokens=4000)
    except Exception:
        return {}
    rows = list(out.get("relation_fixes") or out.get("relation_writings") or [])
    by_id: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        cid = (r.get("candidate_id") or "").strip()
        if not cid:
            continue
        patch: dict[str, Any] = {}
        if "title" in r and r.get("title") is not None:
            patch["title"] = str(r.get("title") or "").strip()
        if "body" in r and r.get("body") is not None:
            patch["body"] = str(r.get("body") or "").strip()
        if patch:
            by_id[cid] = patch
    return by_id


def recover_ungrounded_bodies(relations: list[dict], *, max_rounds: int = 1) -> list[dict]:
    """body 因 ungrounded 被抹且仍有 details → 再 rewrite 最多 max_rounds 轮。"""
    from .relation_verify import body_summary_grounded
    from .relation_writer_audit import append_event, new_trace

    rels = [dict(r) for r in (relations or []) if isinstance(r, dict)]
    if not rels or max_rounds < 1:
        return rels

    for round_i in range(max_rounds):
        tasks: list[dict] = []
        for rel in rels:
            body = (rel.get("body") or "").strip()
            details = [str(d).strip() for d in (rel.get("details") or []) if str(d).strip()]
            needs = bool(rel.get("_body_omitted_ungrounded")) and (not body) and bool(details)
            if not needs:
                continue
            cid = (rel.get("candidate_id") or "").strip() or f"anon-{id(rel)}"
            if not (rel.get("candidate_id") or "").strip():
                rel["candidate_id"] = cid
            tasks.append({
                "candidate_id": cid,
                "label": rel.get("label"),
                "label_hint": label_write_hint(rel.get("label") or ""),
                "evidence": list(rel.get("evidence") or [])[:6],
                "details_locked": details[:6],
                "fix_codes": ["body_ungrounded", "body_empty"],
                "fix_fields": ["body"],
                "bad_title": (rel.get("title") or "")[:120],
                "bad_body": "",
            })
        if not tasks:
            break
        patches = call_writer_field_rewrite(tasks)
        if not patches:
            break
        for rel in rels:
            cid = (rel.get("candidate_id") or "").strip()
            patch = patches.get(cid) or {}
            new_body = (patch.get("body") or "").strip() if "body" in patch else ""
            if not new_body:
                continue
            trace = rel.get("_writer_trace") if isinstance(rel.get("_writer_trace"), dict) else new_trace(cid)
            append_event(
                trace,
                "ungrounded_rewrite",
                round=round_i + 1,
                new_body=new_body,
            )
            rel["_writer_trace"] = trace
            prev = list(rel.get("_writer_field_rewrite") or [])
            if "body" not in prev:
                prev.append("body")
            rel["_writer_field_rewrite"] = prev
            rel["_writer_rewrite_rounds"] = int(rel.get("_writer_rewrite_rounds") or 0) + 1
            # 再过 hygiene + grounding
            title, body, details, flags = enforce_narrative_hygiene(
                title=rel.get("title") or "",
                body=new_body,
                details=list(rel.get("details") or []),
                candidate_title=rel.get("candidate_title") or rel.get("title") or "",
                evidence=list(rel.get("evidence") or []),
            )
            rel["title"] = title
            rel["details"] = details
            if flags.get("body_cleared_template") or flags.get("body_cleared_label_leak"):
                rel["body"] = ""
                rel["_body_omitted_template"] = True
                continue
            if body and body_summary_grounded(body, rel):
                rel["body"] = body
                rel.pop("_body_omitted_ungrounded", None)
                append_event(trace, "ungrounded_recovered", body=body)
            else:
                rel["body"] = ""
                rel["_body_omitted_ungrounded"] = True
                append_event(trace, "ungrounded_still_fail", attempted=body or new_body)
            rel["_writer_trace"] = trace
    return rels


def write_relations(
    relation_objects: list[dict],
    writings: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """RelationObject 列表 → 合并写作结果；返回 (relations, skipped)。

    流程：一次 Writer → 违规/空 body 字段重写（最多 2 轮）→ 硬闸；
    仍无 body 不截字合成；强卡允许最终 body 为空（有 details 即成卡）。
    """
    from .relation_writer_audit import append_event, attach_trace_to_rel, new_trace

    objects = [relation_object_from_gate(o) for o in relation_objects if isinstance(o, dict)]
    if writings is None and objects:
        writings = call_writer_llm(objects)
    by_id = {
        (w.get("candidate_id") or "").strip(): w
        for w in (writings or [])
        if isinstance(w, dict)
    }

    drafted: list[tuple[dict, dict]] = []
    for obj in objects:
        cid = (obj.get("candidate_id") or "").strip()
        w = by_id.get(cid) or {}
        rel = merge_writing(obj, w, apply_hygiene=False)
        trace = new_trace(cid)
        append_event(
            trace,
            "first_write",
            title=rel.get("title") or "",
            body=rel.get("body") or "",
            n_details=len([d for d in (rel.get("details") or []) if str(d).strip()]),
        )
        rel["_writer_trace"] = trace
        drafted.append((obj, rel))

    max_rounds = 2
    rewrite_rounds = 0
    for _ in range(max_rounds):
        rewrite_tasks: list[dict] = []
        for obj, rel in drafted:
            cid = (obj.get("candidate_id") or "").strip()
            codes = narrative_violation_codes(
                rel.get("title") or "",
                rel.get("body") or "",
                list(rel.get("details") or []),
            )
            if not codes:
                continue
            fields: list[str] = []
            if any(c.startswith("title_") for c in codes):
                fields.append("title")
            if any(c.startswith("body_") for c in codes):
                fields.append("body")
            if not fields:
                continue
            rewrite_tasks.append({
                "candidate_id": cid,
                "label": obj.get("label"),
                "label_hint": label_write_hint(obj.get("label") or ""),
                "evidence": list(rel.get("evidence") or [])[:6],
                "details_locked": list(rel.get("details") or [])[:6],
                "fix_codes": codes,
                "fix_fields": fields,
                "bad_title": (rel.get("title") or "")[:120],
                "bad_body": (rel.get("body") or "")[:240],
            })
            trace = rel.get("_writer_trace") if isinstance(rel.get("_writer_trace"), dict) else new_trace(cid)
            append_event(
                trace,
                "violation",
                codes=codes,
                fields=fields,
                bad_title=rel.get("title") or "",
                bad_body=rel.get("body") or "",
            )
            rel["_writer_trace"] = trace
        if not rewrite_tasks:
            break
        patches = call_writer_field_rewrite(rewrite_tasks)
        rewrite_rounds += 1
        if not patches:
            break
        for obj, rel in drafted:
            cid = (obj.get("candidate_id") or "").strip()
            patch = patches.get(cid) or {}
            if not patch:
                continue
            if "title" in patch and patch["title"]:
                rel["title"] = patch["title"]
            if "body" in patch:
                rel["body"] = patch["body"]
            prev = list(rel.get("_writer_field_rewrite") or [])
            for k in patch.keys():
                if k not in prev:
                    prev.append(k)
            rel["_writer_field_rewrite"] = prev
            rel["_writer_rewrite_rounds"] = rewrite_rounds
            trace = rel.get("_writer_trace") if isinstance(rel.get("_writer_trace"), dict) else new_trace(cid)
            append_event(
                trace,
                "field_rewrite",
                round=rewrite_rounds,
                new_title=patch.get("title") or "",
                new_body=patch.get("body") if "body" in patch else None,
            )
            rel["_writer_trace"] = trace
    out: list[dict] = []
    skipped: list[dict] = []
    for obj, rel in drafted:
        cid = (obj.get("candidate_id") or "").strip()
        before_body = (rel.get("body") or "").strip()
        rel = _apply_hygiene_to_rel(rel, obj)
        trace = rel.get("_writer_trace") if isinstance(rel.get("_writer_trace"), dict) else new_trace(cid)
        if rel.get("_body_omitted_template") or (before_body and not (rel.get("body") or "").strip()):
            append_event(
                trace,
                "hygiene",
                cleared_template=bool(rel.get("_body_omitted_template")),
                title_rebuilt=bool(rel.get("_title_rebuilt_from_details")),
                body_after=rel.get("body") or "",
            )
        rel["_writer_trace"] = trace
        title_ok = bool((rel.get("title") or "").strip())
        body_ok = bool((rel.get("body") or "").strip())
        details_ok = any(str(d).strip() for d in (rel.get("details") or []))
        if not title_ok or not (body_ok or details_ok):
            append_event(trace, "skipped", reason="missing_narrative_title_or_body")
            skipped.append({
                "candidate_id": cid,
                "reason": "missing_narrative_title_or_body",
            })
            continue
        out.append(attach_trace_to_rel(rel, trace))
    return _dedupe_relations_by_title(out), skipped


def fact_snapshot(obj: dict) -> dict[str, Any]:
    """供 pipeline 在 verify/filter 后还原锁字段。"""
    return _locked_snapshot(relation_object_from_gate(obj))
