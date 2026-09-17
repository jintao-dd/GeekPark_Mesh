"""Relation Decision：Candidate → Decision → Evidence Gate → RelationObject。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .aggregator import sanitize_owner_team
from .edm import FLAG_COLORS
from .owner_guard import cross_team_provenance_ok, filter_draft_relations
from .relation_candidates import (
    _align_relation_from_evidence,
    _build_evidence,
    _facts_consistent,
    _items_index,
    _sources_from_evidence,
    _teams_from_evidence,
    _title_entities,
    build_relation_candidates,
    candidate_build_stats,
    candidate_canonical_audit,
)
from .relation_decision_audit import (
    GATE_CODE_DECISION_INCONSISTENT,
    GATE_CODE_DECISION_MISSING,
    GATE_CODE_INVALID_CAND,
    GATE_CODE_INVALID_LABEL,
    GATE_CODE_LLM_SKIP,
    GATE_CODE_MERE_MENTION,
    GATE_CODE_MISMATCH,
    GATE_CODE_MISSING_REFS,
    GATE_CODE_NO_SHARED_ANCHOR,
    GATE_CODE_PROVENANCE,
    GATE_CODE_PURE_CO,
    GATE_CODE_REFS_BAD,
    GATE_CODE_SINGLE_TEAM,
    finalize_decision_audit,
    gate_reason_detail,
    parse_gate_reason_code,
)
from .relation_decision_consistency import (
    check_decision_consistency,
    infer_relation_type,
    normalize_decision,
    normalize_relation_label,
)
from .relation_display import attach_reader_flags, display_summary, split_relations_for_publish
from .relation_writer import fact_snapshot, write_relations
from .relation_verify import editorial_weak, verify_relations_narratives
from .relation_claim_check import apply_claim_checks

VALID_LABELS = frozenset(FLAG_COLORS.keys())


def assign_candidate_ids(candidates: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(candidates):
        row = dict(c)
        row["candidate_id"] = f"c{i + 1}"
        out.append(row)
    return out


def _candidate_item_set(cand: dict) -> set[int]:
    ids: set[int] = set()
    for iid in cand.get("item_ids") or []:
        if iid is not None:
            ids.add(int(iid))
    for tf in cand.get("team_facts") or []:
        for iid in tf.get("item_ids") or []:
            if iid is not None:
                ids.add(int(iid))
    return ids


def is_pure_entity_cooccurrence(cand: dict) -> bool:
    """标题为「主实体 · 共现实体」但次实体未形成跨团队事链 → 纯共现。

    2026-09 修正：用 canonical key 比较实体，避免 "FounderPark" / "Founder Park"
    写法差异导致误判；同时要求主实体在两 team_facts snippet 中确实都出现。
    """
    from .entity_canonicalizer import canonical_key

    title = (cand.get("title") or "").strip()
    if " · " not in title:
        return False
    parts = [p.strip() for p in title.split(" · ", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    primary, secondary = parts
    primary_key = canonical_key(primary)
    secondary_key = canonical_key(secondary)

    team_hits: dict[str, set[str]] = {}
    for tf in cand.get("team_facts") or []:
        team = tf.get("team") or ""
        blob = " ".join(tf.get("snippets") or [])
        blob_key = canonical_key(blob)
        hit = set()
        if primary and (primary in blob or primary_key in blob_key):
            hit.add("primary")
        if secondary and (secondary in blob or secondary_key in blob_key):
            hit.add("secondary")
        if hit:
            team_hits[team] = hit
    if len(team_hits) < 2:
        return False
    # 两团队都提到 primary，但 secondary 至多在一方出现 → 共现绑定
    all_hits = set().union(*team_hits.values()) if team_hits else set()
    if "primary" not in all_hits:
        return False
    sec_teams = sum(1 for h in team_hits.values() if "secondary" in h)
    if sec_teams <= 1 and "secondary" in all_hits:
        return True
    return False


def _anchor_tokens(text: str) -> set[str]:
    """从标题/snippet 抽可用于跨队对齐的锚点（公司/人/项目名）。"""
    import re

    s = text or ""
    out: set[str] = set()
    for m in re.findall(r"[A-Za-z][A-Za-z0-9][A-Za-z0-9+.\-]{1,}", s):
        if len(m) >= 2:
            out.add(m.lower())
    for m in re.findall(r"[\u4e00-\u9fff]{2,12}", s):
        # 过泛的关系/流程词不当锚点
        if m in (
            "商业化", "编辑部", "团队", "例会", "选题", "沟通", "记录", "进行中",
            "受访者", "非核实", "事实", "会议", "判断", "本周", "上周", "发布",
        ):
            continue
        out.add(m)
    return out


def _anchor_tokens_for_evidence(evidence: list[dict], title: str = "") -> set[str]:
    """从 evidence snippets 提取锚点，同时考虑 canonical 实体别名。"""
    from .entity_canonicalizer import canonical_key

    tokens: set[str] = set()
    for e in evidence or []:
        if not isinstance(e, dict):
            continue
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if not snip:
            continue
        tokens.update(_anchor_tokens(snip))
        # 额外把 snippet 里能命中 canonical key 的实体也加进去
        for tok in list(tokens):
            ck = canonical_key(tok)
            if ck and ck != tok.lower():
                tokens.add(ck)
        # 对整句 snippet 尝试 canonical_key，并把结果拆成子串也加进去（处理中文连续词）
        ck = canonical_key(snip)
        if ck:
            tokens.add(ck)
            # 把 canonical key 按已知实体规则再拆：如 "gp拟拜访" -> 包含 "poko" 等
            for canon_key, aliases in __import__("app.entity_canonicalizer", fromlist=["_HARDCODED_ALIAS_RULES"])._HARDCODED_ALIAS_RULES.items():
                if canon_key in ck:
                    tokens.add(canon_key)
    if title:
        tokens.update(_anchor_tokens(title))
        ck = canonical_key(title)
        if ck and ck != title.lower():
            tokens.add(ck)
    return tokens


def evidence_shared_anchor(evidence: list[dict], *, title: str = "") -> bool:
    """两侧 evidence 必须共享至少一个实体锚点（优先标题主实体）。

    质量优先：仅「两队都有字」不够；必须是同一公司/人/项目上的双边事实。

    2026-09 修正：
    - 标题主实体经 canonical key 后在 snippet 里命中子串/别名即算共享。
    - 若 snippet 过短未含实体，但 evidence 引用的 item 实体与标题 canonical key 一致，
      也视为共享锚点（避免测试/生产里的短占位 snippet 误杀）。
    """
    from .entity_canonicalizer import canonical_key

    by_team: dict[str, str] = {}
    item_entities_by_team: dict[str, set[str]] = {}
    for e in evidence or []:
        if not isinstance(e, dict):
            continue
        team = (e.get("team") or "").strip()
        if not team or team.startswith("→") or team.startswith("->"):
            continue
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if snip:
            by_team[team] = (by_team.get(team) or "") + "\n" + snip
        # 收集 evidence 引用的 item 原始实体（用于 snippet 过短兜底）
        item_ents = e.get("_item_entities") or e.get("entities") or []
        if isinstance(item_ents, str):
            try:
                item_ents = json.loads(item_ents)
            except (json.JSONDecodeError, TypeError):
                item_ents = []
        keys = {canonical_key(str(x)) for x in item_ents if str(x).strip()}
        item_entities_by_team.setdefault(team, set()).update(keys)
    if len(by_team) < 2 and len(item_entities_by_team) < 2:
        return False

    title_s = (title or "").strip()
    # 标题主实体：取 · / ： 前一段
    primary = title_s
    for sep in (" · ", " / ", "：", ":"):
        if sep in primary:
            primary = primary.split(sep, 1)[0].strip()
            break
    primary = primary.strip()
    primary_key = canonical_key(primary) if primary and len(primary) >= 2 else ""

    if primary_key:
        # 1) snippet 里直接包含主实体或其 canonical key
        hits = 0
        for team, blob in by_team.items():
            if primary in blob or primary.lower() in blob.lower() or _canonical_key_in_blob(primary_key, blob):
                hits += 1
            elif primary_key in item_entities_by_team.get(team, set()):
                # 2) evidence 引用的 item 实体与标题 canonical key 一致
                hits += 1
        if hits >= 2:
            return True

    team_toks = [_anchor_tokens_for_evidence([{"snippet": blob}]) for blob in by_team.values()]
    if team_toks:
        shared = set.intersection(*team_toks)
        if shared:
            return True

    # 3) 按 evidence item 实体做 canonical 交集兜底
    if len(item_entities_by_team) >= 2:
        shared_entities = set.intersection(*item_entities_by_team.values())
        if shared_entities:
            return True

    return False


def _canonical_key_in_blob(key: str, blob: str) -> bool:
    """canonical key 是否被 blob 包含（支持 key 是某实体子串，且 blob 里出现该实体）。"""
    from .entity_canonicalizer import canonical_key, _HARDCODED_ALIAS_RULES

    if not key or not blob:
        return False
    # 直接子串
    if key in blob.lower():
        return True
    blob_key = canonical_key(blob)
    if key in blob_key:
        return True
    # 反向：key 对应的实体名是否作为子串出现在 blob 里
    # 这里只处理硬规则里的已知实体名
    for canon_key, aliases in _HARDCODED_ALIAS_RULES.items():
        if canon_key != key:
            continue
        for alias in aliases:
            if alias and alias in blob:
                return True
    return False


def _entity_for_provenance(cand: dict) -> str:
    title = (cand.get("title") or "").strip()
    if " · " in title:
        return title.split(" · ", 1)[0].strip()
    return title


def _evidence_from_refs(
    cand: dict,
    evidence_refs: list[int],
    items_by_id: dict[int, dict],
) -> list[dict]:
    allowed = _candidate_item_set(cand)
    refs = [int(x) for x in evidence_refs if x is not None and int(x) in allowed]
    if not refs:
        return []
    slim = dict(cand)
    slim_team_facts = []
    for tf in cand.get("team_facts") or []:
        tf = dict(tf)
        tf["item_ids"] = [i for i in (tf.get("item_ids") or []) if int(i) in refs]
        slim_team_facts.append(tf)
    slim["team_facts"] = slim_team_facts
    slim["item_ids"] = refs
    ev = _build_evidence(slim, items_by_id)
    return [e for e in ev if int(e.get("item_id") or 0) in refs]


def _code_suggested_teams(cand: dict, evidence: list[dict], label: str) -> list[str]:
    ev_teams = set(_teams_from_evidence(evidence))
    solid: list[str] = []
    pre_suggested: list[str] = []
    for t in cand.get("teams") or []:
        s = str(t).strip()
        if s.startswith("→") or s.startswith("->"):
            inner = sanitize_owner_team(s.lstrip("→").lstrip("->").strip()) or s.lstrip("→").lstrip("->").strip()
            if inner:
                pre_suggested.append(f"→ {inner}")
        else:
            ot = sanitize_owner_team(s) or s
            if ot:
                solid.append(ot)
    for rt in cand.get("routing_targets") or []:
        rt = sanitize_owner_team(rt) or rt
        if rt:
            badge = f"→ {rt}"
            if badge not in pre_suggested:
                pre_suggested.append(badge)
    if cand.get("candidate_kind") == "routing" or cand.get("routing_targets"):
        return list(dict.fromkeys(pre_suggested))
    missing = [t for t in solid if t not in ev_teams]
    hints = ("一方接触", "用得上", "尚未接触", "海外", "报道了")
    out = list(dict.fromkeys(pre_suggested))
    if any(h in label for h in hints):
        out.extend(f"→ {t}" for t in missing if f"→ {t}" not in out)
    return out


def _derive_weak(label: str, cand: dict, evidence: list[dict]) -> bool:
    if bool(cand.get("weak")):
        return True
    if editorial_weak({"label": label}):
        return True
    solid = _teams_from_evidence(evidence)
    if len(solid) >= 2:
        return False
    return editorial_weak({"label": label})


def _slim_team_facts(cand: dict) -> list[dict]:
    out: list[dict] = []
    for tf in cand.get("team_facts") or []:
        if not isinstance(tf, dict):
            continue
        out.append({
            "team": tf.get("team"),
            "item_ids": list(tf.get("item_ids") or []),
            "snippets": [(s or "")[:200] for s in (tf.get("snippets") or [])[:3]],
        })
    return out


def _missing_decision_row(cand: dict) -> dict[str, Any]:
    cid = (cand.get("candidate_id") or "").strip()
    title = (cand.get("title") or "").strip()
    return {
        "candidate_id": cid,
        "candidate_title": title or None,
        "decision_candidate": True,
        "llm_decision": None,
        "decision_outcome": "missing",
        "decision_tier": None,
        "llm_label": "",
        "llm_reason": "",
        "relation_type": None,
        "llm_evidence_refs": [],
        "decision_missing": True,
        "gate_decision": "skip",
        "gate_outcome": "skip",
        "gate_reason": GATE_CODE_DECISION_MISSING,
        "gate_reason_code": GATE_CODE_DECISION_MISSING,
        "gate_reason_detail": "LLM 未返回该 candidate 的 relation_decision",
        "gate_would_cooccur": False,
        "gate_overrode_llm": False,
    }


def apply_evidence_gate(
    decisions: list[dict],
    candidates: list[dict],
    items: list[dict],
    *,
    missing_ids: set[str] | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Decision + 代码 Gate → locked relation 骨架 + 审计日志。"""
    by_id = {c["candidate_id"]: c for c in candidates if c.get("candidate_id")}
    items_by_id = _items_index(items)
    approved: list[dict] = []
    audit_rows: list[dict] = []

    decisions_by_id: dict[str, dict] = {}
    for d in decisions:
        if not isinstance(d, dict):
            continue
        nd = normalize_decision(d)
        cid = (nd.get("candidate_id") or "").strip()
        if cid and cid not in decisions_by_id:
            decisions_by_id[cid] = nd

    missing = set(missing_ids or [])
    if not missing:
        from .llm import missing_decision_ids
        missing = set(missing_decision_ids(candidates, decisions))

    for cand in candidates:
        cid = (cand.get("candidate_id") or "").strip()
        if not cid:
            continue
        if cid in missing or cid not in decisions_by_id:
            audit_rows.append(_missing_decision_row(cand))
            continue

        d = decisions_by_id[cid]
        decision = (d.get("decision") or "").strip().lower()
        label = normalize_relation_label((d.get("label") or "").strip())
        reason = (d.get("reason") or "").strip()
        relation_type = (d.get("relation_type") or "").strip() or infer_relation_type(label, reason)
        decision_tier = (d.get("decision_tier") or "").strip().lower()
        if decision == "keep" and (not decision_tier or decision_tier == "skip"):
            from .relation_display import normalize_decision_tier
            decision_tier = normalize_decision_tier(
                "keep", label, None, relation_type=relation_type,
            )
        refs = d.get("evidence_refs") or []

        row: dict[str, Any] = {
            "candidate_id": cid,
            "candidate_title": cand.get("title"),
            "decision_candidate": True,
            "llm_decision": decision,
            "decision_outcome": decision,
            "decision_tier": decision_tier or None,
            "llm_label": label,
            "llm_reason": reason,
            "relation_type": relation_type or None,
            "llm_evidence_refs": list(refs),
            "gate_decision": "skip",
            "gate_outcome": "skip",
            "gate_reason": "",
            "gate_reason_code": "",
            "gate_reason_detail": "",
            "gate_would_cooccur": False,
            "gate_overrode_llm": False,
        }

        def _skip(code: str, *, detail: str | None = None, overrode: bool = True) -> None:
            row["gate_reason_code"] = code
            row["gate_reason"] = detail or code
            row["gate_reason_detail"] = gate_reason_detail(code, row["gate_reason"], reason)
            row["gate_overrode_llm"] = overrode and decision == "keep"
            row["gate_outcome"] = "skip"
            audit_rows.append(row)

        if decision != "keep":
            row["decision_tier"] = "skip"
            row["gate_reason_code"] = GATE_CODE_LLM_SKIP
            row["gate_reason"] = reason or GATE_CODE_LLM_SKIP
            row["gate_reason_detail"] = gate_reason_detail(GATE_CODE_LLM_SKIP, row["gate_reason"], reason)
            row["gate_overrode_llm"] = False
            audit_rows.append(row)
            continue

        # 编辑一致性：只记 warning，不 hard-block（Gate = 事实安全）
        inconsistent = check_decision_consistency(
            label=label, reason=reason, relation_type=relation_type or None,
        )
        if inconsistent:
            row["gate_warning"] = GATE_CODE_DECISION_INCONSISTENT
            row["gate_warning_detail"] = inconsistent

        if label not in VALID_LABELS:
            _skip(GATE_CODE_INVALID_LABEL, detail=f"invalid_label:{label or '(empty)'}")
            continue

        if not refs:
            _skip(GATE_CODE_MISSING_REFS)
            continue

        evidence = _evidence_from_refs(cand, refs, items_by_id)
        if not evidence:
            _skip(GATE_CODE_REFS_BAD)
            continue

        # 仅点名/身份介绍、无真实沟通对接 → 硬 skip（不成卡）
        from .relation_gate import evidence_has_real_engagement

        if not evidence_has_real_engagement(
            evidence,
            title=(cand.get("title") or ""),
        ):
            _skip(GATE_CODE_MERE_MENTION, detail="mere_mention_no_engagement")
            continue

        ev_teams = _teams_from_evidence(evidence)
        # 产品：关系卡 = 至少两个实线团队都有 evidence，或
        # 单边 watch/海外路由（1 队实线 + 1 队虚线建议）允许成卡（但不进入读者可见）。
        suggested_teams = _code_suggested_teams(cand, evidence, label)
        solid_plus_suggested = set(ev_teams) | {sanitize_owner_team(t.lstrip("→").lstrip("->").strip()) for t in suggested_teams if t}
        if len(ev_teams) < 2 and len(solid_plus_suggested) < 2:
            _skip(GATE_CODE_SINGLE_TEAM)
            continue
        entity = _entity_for_provenance(cand)
        if entity and not cross_team_provenance_ok(items, entity, ev_teams):
            _skip(GATE_CODE_PROVENANCE)
            continue

        # 质量优先：纯共现 / 无共享锚点 → 不再硬 skip，改为 warning 并进入 backlog。
        # 这样 LLM keep 的高价值候选不会被代码 gate 误杀；readers 见只负责可见性分级。
        gate_warnings: list[str] = []
        would_cooccur = is_pure_entity_cooccurrence(cand)
        if would_cooccur:
            gate_warnings.append(GATE_CODE_PURE_CO)
        title_for_anchor = (cand.get("title") or "") or entity
        has_anchor = evidence_shared_anchor(evidence, title=title_for_anchor)
        if not has_anchor:
            gate_warnings.append(GATE_CODE_NO_SHARED_ANCHOR)

        suggested = _code_suggested_teams(cand, evidence, label)
        weak = _derive_weak(label, cand, evidence)
        locked = {
            "candidate_id": cid,
            "candidate_title": cand.get("title") or "",
            "label": label,
            "relation_type": relation_type or None,
            "decision_tier": decision_tier,
            "weak": weak,
            "teams": ev_teams + suggested,
            "sources": _sources_from_evidence(evidence),
            "evidence": evidence,
            "item_ids": sorted({int(e["item_id"]) for e in evidence if e.get("item_id") is not None}),
            "team_facts": _slim_team_facts(cand),
            "provenance_ok": True,
            "gate_would_cooccur": False,
            "needs_review": True,
            "status": "needs_review",
            "decision_reason": reason,
            "relation_reason": reason,
        }
        # 若存在纯共现/无共享锚点 warning，强制降级为 watch backlog，不进入 reader。
        if gate_warnings:
            locked["weak"] = True
            locked["decision_tier"] = "watch"
            locked["status"] = "needs_review"
            locked["_gate_warnings"] = gate_warnings

        locked = _align_relation_from_evidence(locked, suggested=suggested)
        if not _facts_consistent(locked):
            _skip(GATE_CODE_MISMATCH)
            continue

        # 对警告降级卡追加 audit row 标注：gate 未硬 skip，但标记为 needs_review/backlog
        row["gate_decision"] = "keep"
        row["gate_outcome"] = "keep"
        row["gate_reason"] = reason
        row["gate_reason_code"] = "gate_pass"
        row["gate_reason_detail"] = reason
        row["gate_would_cooccur"] = would_cooccur
        row["gate_overrode_llm"] = False
        if gate_warnings:
            # 让 ledger 能识别出这是 "gate 放行但 warning 降级"，而不是完全正常
            row["gate_reason_code"] = gate_warnings[0]
            row["gate_reason_detail"] = (
                "Gate 放行但带 warning：" + ", ".join(gate_warnings)
                + "；LLM 理由：" + (reason or "")
            )
        row["n_evidence"] = len(evidence)
        row["teams"] = locked.get("teams")
        row["label"] = label
        row["decision_tier"] = locked.get("decision_tier") or decision_tier
        audit_rows.append(row)
        approved.append(locked)

    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_candidates": len(candidates),
        "n_llm_decisions": len(decisions_by_id),
        "n_decision_missing": len(missing),
        "n_keep_gate": sum(1 for r in audit_rows if r.get("gate_decision") == "keep"),
        "n_skip_gate": sum(1 for r in audit_rows if r.get("gate_decision") == "skip"),
        "missing_decision_ids": sorted(missing),
        "rows": audit_rows,
    }
    return approved, audit


def _gate_fact_snapshot(rel: dict) -> dict[str, Any]:
    return fact_snapshot(rel)


def _restore_gate_facts(relations: list[dict], snapshots: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for r in relations:
        if not isinstance(r, dict):
            continue
        cid = (r.get("candidate_id") or "").strip()
        snap = snapshots.get(cid)
        if snap:
            rel = dict(r)
            rel.update(snap)
            out.append(rel)
        else:
            out.append(r)
    return out


def _strip_external_weak_duplicates(relations: list[dict]) -> list[dict]:
    """去掉无 evidence 的 external/weak 重复卡（与 candidate 卡同标题实体）。"""
    seen_entities: list[set[str]] = []
    out: list[dict] = []
    for r in relations:
        if not isinstance(r, dict):
            continue
        ents = _title_entities(r.get("title") or "")
        ev = r.get("evidence") or []
        is_orphan_weak = (not ev) and (r.get("weak") or editorial_weak(r))
        if is_orphan_weak:
            if any(ents and ents & old for old in seen_entities):
                continue
            continue
        if ents:
            dup = False
            for old in seen_entities:
                if ents & old:
                    dup = True
                    break
            if dup:
                continue
            seen_entities.append(ents)
        out.append(r)
    return out


def build_relations_two_phase(
    draft: dict,
    candidates: list[dict],
    items: list[dict],
    *,
    team_cards: list[dict] | None = None,
    decisions: list[dict] | None = None,
    writings: list[dict] | None = None,
    narratives: list[dict] | None = None,
) -> dict:
    """Orchestrator：Decision → Gate → RelationObject → Writer → Claim Check → Verify。"""
    from . import llm
    from .issue_verify import verify_issue_draft

    # 兼容旧参数名 narratives
    if writings is None:
        writings = narratives

    data = dict(draft)
    cands = assign_candidate_ids(list(candidates))
    stats = candidate_build_stats(items)
    # 把候选生成阶段的 canonical 归一审计挂到 data，便于追踪
    data["_relation_canonical_audit"] = candidate_canonical_audit(cands)
    # 候选生成人类可读摘要
    data["_relation_candidate_summary"] = {
        "n_cooccurrence": stats.get("raw_cooccurrence", 0),
        "n_routing": stats.get("raw_routing", 0),
        "n_card_bridge": stats.get("raw_card_bridge", 0),
        "n_total": stats.get("raw", 0),
        "canonical_rules_matched": len(
            (data.get("_relation_canonical_audit") or {}).get("rules_matched") or []
        ),
        "canonical_llm_groups": len(
            (data.get("_relation_canonical_audit") or {}).get("llm_groups") or []
        ),
    }

    if decisions is None:
        coverage_error: llm.RelationDecisionCoverageError | None = None
        try:
            decisions, decision_coverage = llm.build_relation_decisions_with_coverage(
                cands, team_cards or [],
            )
        except llm.RelationDecisionCoverageError as e:
            coverage_error = e
            decisions = e.decisions
            decision_coverage = e.meta
    else:
        coverage_error = None
        from .llm import missing_decision_ids
        decision_coverage = {
            "n_candidates": len(cands),
            "n_decisions_received": len(decisions),
            "missing_ids": missing_decision_ids(cands, decisions),
            "coverage_ok": not missing_decision_ids(cands, decisions),
        }

    missing_ids = set(decision_coverage.get("missing_ids") or [])
    relation_objects, audit = apply_evidence_gate(
        decisions, cands, items, missing_ids=missing_ids,
    )
    audit["decision_coverage"] = decision_coverage
    audit["n_candidates_raw"] = stats["raw"]
    audit["n_candidates_deduped"] = stats["deduped"]
    audit["n_candidates_for_decision"] = stats["for_llm"]

    gate_snapshots = {
        r["candidate_id"]: _gate_fact_snapshot(r)
        for r in relation_objects
        if r.get("candidate_id")
    }

    rels, write_skipped = write_relations(relation_objects, writings)
    audit["n_write_skipped"] = len(write_skipped)
    audit["write_skipped"] = write_skipped
    audit["n_narrative_skipped"] = len(write_skipped)
    audit["narrative_skipped"] = write_skipped
    from .relation_writer_audit import finalize_writer_audit, merge_writer_audit
    data = merge_writer_audit(data, finalize_writer_audit(rels, skipped=write_skipped))

    # Claim Check：必须吃 Writer 原文，再进入会改写 body 的 narrative verify。
    # 契约：Evidence Gate 未通过 / 无 evidence 的卡不得进入 Claim Check
    # （Gate 已保证 approved 有 evidence；此处再防错接与 legacy 注入）。
    rels = _strip_external_weak_duplicates(rels)
    gated_for_claim: list[dict] = []
    n_empty_ev = 0
    for r in rels:
        if not isinstance(r, dict):
            continue
        if not (r.get("evidence") or []):
            n_empty_ev += 1
            continue
        gated_for_claim.append(r)
    audit["n_skipped_empty_evidence_before_claim"] = n_empty_ev
    rels, claim_audit = apply_claim_checks(gated_for_claim, items)
    audit["claim_check"] = claim_audit
    data["_relation_claim_audit"] = claim_audit

    rels = verify_relations_narratives(rels)
    rels = attach_reader_flags(_restore_gate_facts(rels, gate_snapshots))
    reader_rels, backlog_rels = split_relations_for_publish(rels)
    data["relations"] = rels
    data["_relations_reader"] = reader_rels
    data["_relations_backlog"] = backlog_rels
    audit["display_summary"] = display_summary(rels)
    data = filter_draft_relations(data, items)
    data["relations"] = attach_reader_flags(_restore_gate_facts(data.get("relations") or [], gate_snapshots))
    reader_rels, backlog_rels = split_relations_for_publish(data["relations"])
    data["_relations_reader"] = reader_rels
    data["_relations_backlog"] = backlog_rels
    # 最终：无 evidence 不得留（draft 全量 backlog）
    data["relations"] = [
        r for r in (data.get("relations") or [])
        if isinstance(r, dict) and (r.get("evidence") or [])
    ]
    reader_rels, backlog_rels = split_relations_for_publish(data["relations"])
    data["_relations_reader"] = reader_rels
    data["_relations_backlog"] = backlog_rels
    kept_ids = {(r.get("candidate_id") or "").strip() for r in data["relations"]}
    audit["post_gate_drops"] = [
        {
            "candidate_id": r["candidate_id"],
            "candidate_title": r.get("candidate_title"),
            "reason": "filtered_after_gate",
        }
        for r in relation_objects
        if r.get("candidate_id") and r["candidate_id"] not in kept_ids
    ]
    audit["n_draft_relations"] = len(data["relations"])
    audit["n_reader_relations"] = len(reader_rels)
    audit["n_backlog_relations"] = len(backlog_rels)
    data["_relation_decision_audit"] = finalize_decision_audit(audit, data["relations"])
    if data["_relation_decision_audit"].get("outcome_summary") is not None:
        data["_relation_decision_audit"]["outcome_summary"]["n_candidates_raw"] = stats["raw"]
        data["_relation_decision_audit"]["outcome_summary"]["n_candidates_for_decision"] = stats["for_llm"]
        data["_relation_decision_audit"]["outcome_summary"]["claim_check"] = {
            "mode": claim_audit.get("mode"),
            "n_invalid": claim_audit.get("n_invalid"),
            "n_dropped_enforce": claim_audit.get("n_dropped_enforce"),
        }
        # 人类可读漏斗摘要
        summary = data["_relation_decision_audit"]["outcome_summary"]
        summary["human_summary"] = {
            "候选总数": summary.get("n_candidates", 0),
            "LLM漏答": summary.get("n_decision_missing", 0),
            "LLM主动skip": summary.get("n_skipped_llm", 0),
            "Gate放行(含warning降级)": summary.get("n_draft_relations", 0) + summary.get("n_dropped_narrative", 0),
            "读者可见": summary.get("n_reader_relations", 0),
            "Backlog/watch": summary.get("n_backlog_relations", 0),
            "Gate硬拦截": summary.get("n_skipped_gate_override", 0),
            "纯共现warning": summary.get("n_gate_would_cooccur", 0),
        }
    out = verify_issue_draft(data, items, team_cards=team_cards)
    if out.get("_relation_decision_audit"):
        out["_relation_decision_audit"] = finalize_decision_audit(
            out["_relation_decision_audit"], out.get("relations") or [],
        )
    if out.get("_relation_claim_audit") is None and claim_audit:
        out["_relation_claim_audit"] = claim_audit
    if coverage_error is not None:
        raise coverage_error
    return out

