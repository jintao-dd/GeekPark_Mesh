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

_DETAIL_TEAM = re.compile(r"^(.+?)(?:记录|：|:)")

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
    return {
        "candidate_id": obj.get("candidate_id"),
        "label": obj.get("label"),
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
        "对每个 candidate_id 写一条；不得修改 label/teams/evidence。"
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
        if not rel.get("title") or not rel.get("body"):
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
