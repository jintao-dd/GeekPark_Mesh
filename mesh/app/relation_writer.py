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
    "两处记录待核对": "并列冲突点、不选边；body 只点「待核对」，细节各写各的记录原文要点。",
    "一方有需求，另一方尚未接触": "写清有需求侧已发生事实；另一侧仅写 evidence 明确记录的相关事实或「尚未接触」状态，不补充接触推断，不写「该谁去接」的路由建议。",
    "一方接触，另一方用得上": "只写接触/发现侧已发生事实；禁止「记录标注××用得上」等元话（承接方看虚线团队）。",
    "海外接触，国内可能承接": "海外动作须有 evidence；国内侧仅写已有事实，无国内证据就不要写「将承接/可承接」。",
    "海外新发现，国内尚未接触": "写海外新发现事实；国内侧仅写 evidence 明确记录的相关事实或「尚未接触」状态，不根据缺失证据推断国内未接触。",
    "外部在热聊，我们还没碰": "外部热度与内部未接触须都有可引用依据；不写成我们已在跟进或已联动。",
    "已排期，内容侧待安排": "写清已排期事实与内容侧待安排的现状；不写成内容已产出或已联动完成。",
}

_DETAIL_TEAM = re.compile(r"^(.+?)(?:记录|：|:)")


def label_write_hint(label: str) -> str:
    """本卡 label → 一行 Writer hint；未知标签返回空串。"""
    from .relation_decision_consistency import normalize_relation_label

    lab = normalize_relation_label((label or "").strip())
    return LABEL_WRITE_HINTS.get(lab, "")

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
        "对每个 candidate_id 写一条；遵守该卡 label_hint；不得修改 label/teams/evidence；"
        "写不出跨队交叉句则 body 留空，只写 details。"
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
