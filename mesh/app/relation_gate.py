"""上线前关系证据 / 叙事核对闸门。"""
from __future__ import annotations

import json
import re
from typing import Any

from .issue_verify import collect_unsupported_flags
from .owner_guard import (
    cross_team_provenance_ok,
    relation_team_supported,
    solid_team_badges,
    suggested_team_badges,
    _relation_entity_title,
)
from .aggregator import sanitize_owner_team


def _parse_draft(draft_json: str | dict | None) -> dict:
    if isinstance(draft_json, dict):
        return draft_json
    if not draft_json or not str(draft_json).strip():
        return {}
    try:
        return json.loads(draft_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _solid_teams(r: dict) -> list[str]:
    out: list[str] = []
    for t in solid_team_badges(r):
        ot = sanitize_owner_team(t) or str(t).strip()
        if ot and ot not in out:
            out.append(ot)
    return out


def relation_publish_blockers(
    draft_json: str | dict | None,
    items: list[dict],
) -> list[str]:
    """
    发布前关系闸门：
    - 已有 evidence 的 watch/weak 卡可上线（读者页展示全部 grounded）
    - 「→ 团队」虚线不要求该团队有 evidence
    - 双实线团队均须有 evidence / provenance
    - 无 evidence 且非虚线观察卡 → 拦截
    """
    draft = _parse_draft(draft_json)
    rels = draft.get("relations") or []
    if not rels:
        return []

    errs: list[str] = []

    for r in rels:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        label = (r.get("label") or "").strip()
        solid = _solid_teams(r)
        suggested = suggested_team_badges(r)
        has_ev = bool(r.get("evidence") or [])

        # 虚线观察 / 一方接触：有 evidence 即可上线
        if suggested and len(solid) < 2 and has_ev:
            continue
        if (r.get("weak") or r.get("decision_tier") == "watch") and has_ev:
            continue

        if len(solid) >= 2 and title:
            missing = [t for t in solid if not relation_team_supported(items, r, t)]
            if missing:
                errs.append(f"关系「{title}」缺少团队证据：{'、'.join(missing)}")
            else:
                entity_title = _relation_entity_title(r) or title
                if not cross_team_provenance_ok(items, entity_title, solid):
                    errs.append(
                        f"关系「{title}」跨团队出处不足（同源同 pointer 不能冒充两团队各有一手）"
                    )

        details = r.get("details") or []
        sources = r.get("sources") or []
        if not details and not sources and label and not r.get("weak"):
            errs.append(f"关系「{title}」缺少 details/sources，不能作为强关系上线")

        if not has_ev and not suggested:
            errs.append(f"关系「{title}」缺少 evidence[]，不能作为强关系上线")

    return errs


def issue_publish_blockers(draft_json: str | dict | None, items: list[dict]) -> list[str]:
    """关系闸门 + Verify v2 硬拦截（trim 计数仅信息，不拦上线）。"""
    draft = _parse_draft(draft_json)
    errs = relation_publish_blockers(draft, items)
    for flag in collect_unsupported_flags(draft, hard_only=True):
        if flag not in errs:
            errs.append(flag)
    return errs


def _sanitize_relation_narrative(rel: dict) -> dict:
    """成卡收口前：丢掉未 grounded 的 detail 并按队回填；body 用 paraphrase 标准。"""
    from .relation_verify import (
        _backfill_details_by_team,
        _details_from_evidence,
        body_summary_grounded,
        line_grounded,
    )
    from .relation_writer import enforce_narrative_hygiene, strip_route_meta_copy

    out = dict(rel)
    if out.get("body"):
        out["body"] = strip_route_meta_copy(str(out.get("body") or ""))
    details_in = [
        strip_route_meta_copy(str(d))
        for d in (out.get("details") or [])
        if str(d).strip()
    ]
    kept: list[str] = []
    for d in details_in:
        if line_grounded(d, out):
            kept.append(d)
    if not kept:
        kept = _details_from_evidence(out)
        kept = [strip_route_meta_copy(x) for x in kept if x]
    else:
        kept = _backfill_details_by_team(kept, out)
        kept = [strip_route_meta_copy(x) for x in kept if x]
    out["details"] = kept[:8]

    title, body, details, flags = enforce_narrative_hygiene(
        title=out.get("title") or "",
        body=out.get("body") or "",
        details=out["details"],
        candidate_title=out.get("candidate_title") or out.get("title") or "",
        evidence=list(out.get("evidence") or []),
    )
    out["title"] = title
    out["body"] = body
    out["details"] = details
    if flags.get("title_rebuilt"):
        out["_title_rebuilt_from_details"] = True
    if flags.get("body_cleared_template"):
        out["_body_omitted_template"] = True
    if flags.get("body_cleared_restates"):
        out["_body_omitted_restates_details"] = True
    if flags.get("details_deduped"):
        out["_details_deduped"] = True

    body = (out.get("body") or "").strip()
    if body and not body_summary_grounded(body, out):
        out["body"] = ""
        out["_body_omitted_ungrounded"] = True
    return out


def relation_fails_grounding(rel: dict, items: list[dict]) -> list[str]:
    """单张关系卡是否经不起论证（缺团队证据 / 无合格 body·details 等）。

    质量优先：body 用 paraphrase 标准；未 grounded 的 detail 应先被 sanitize 掉，
    不再因单条 detail 字面不够整卡否决。
    """
    if not isinstance(rel, dict):
        return ["非对象关系"]
    from .relation_verify import body_summary_grounded

    errs: list[str] = []
    title = (rel.get("title") or "").strip() or "（无标题）"
    body = (rel.get("body") or "").strip()
    details = [str(d).strip() for d in (rel.get("details") or []) if str(d).strip()]
    weakish = bool(rel.get("weak") or (rel.get("decision_tier") or "").strip().lower() == "watch")

    if weakish and (rel.get("evidence") or []):
        # 观察/弱卡：有 evidence +（body 或 details）即可
        if not body and not details:
            errs.append(f"关系「{title}」缺少 body/details")
    else:
        # 读者强卡：须有 details（来源论证）；body 有更好，空也可成卡
        if not details:
            errs.append(f"关系「{title}」缺少可用 details")
        if body and not body_summary_grounded(body, rel):
            errs.append(f"关系「{title}」body 无法由 evidence 证明")

    mini = {"relations": [rel]}
    for e in relation_publish_blockers(mini, items):
        if e not in errs:
            errs.append(e)
    try:
        from .attribution_verify import scan_draft

        for b in scan_draft(mini, items).blockers:
            if b not in errs:
                errs.append(b)
    except Exception:
        pass
    return errs


def filter_ungrounded_relations(
    draft_json: str | dict | None,
    items: list[dict],
) -> tuple[dict, list[str]]:
    """不够格的关系卡不成卡（静默从 relations 拿掉）。

    产品约定：论证失败 = 根本不生成读者卡，不是「先生成再隐藏」。
    返回 (新草稿, 未成卡标题列表) —— 标题仅供 audit，默认不上 UI。
    """
    draft = dict(_parse_draft(draft_json))
    kept: list[dict] = []
    not_formed: list[str] = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        sanitized = _sanitize_relation_narrative(r)
        if relation_fails_grounding(sanitized, items):
            not_formed.append(title)
            continue
        kept.append(sanitized)
    kept = annotate_suspected_duplicates(kept)
    draft["relations"] = kept
    # 不再写入 _relations_dropped_ungrounded（避免「已隐藏」话术）；仅留静默审计计数
    draft.pop("_relations_dropped_ungrounded", None)
    if not_formed:
        draft["_relations_not_formed_count"] = len(not_formed)
        draft["_relations_not_formed_titles"] = not_formed[:30]
    else:
        draft.pop("_relations_not_formed_count", None)
        draft.pop("_relations_not_formed_titles", None)
    return draft, not_formed


def _primary_entity_key(title: str) -> str:
    t = (title or "").strip()
    for sep in (" · ", " / ", "：", ":"):
        if sep in t:
            t = t.split(sep, 1)[0].strip()
            break
    m = re.match(r"^([\w\u4e00-\u9fffA-Za-z0-9]{2,24})", t)
    return (m.group(1) if m else t[:24]).strip().lower()


def _evidence_item_ids(rel: dict) -> set[int]:
    out: set[int] = set()
    for e in rel.get("evidence") or []:
        if isinstance(e, dict) and e.get("item_id") is not None:
            try:
                out.add(int(e["item_id"]))
            except (TypeError, ValueError):
                pass
    for x in rel.get("item_ids") or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    return out


def annotate_suspected_duplicates(relations: list[dict]) -> list[dict]:
    """疑似重复卡：草稿 Warning，仍展示，交给发布的人决定。

    判定：同一主实体，或 evidence item 重叠度 ≥ 0.5。
    """
    rels = [dict(r) for r in (relations or []) if isinstance(r, dict)]
    n = len(rels)
    if n < 2:
        return rels

    peers: list[set[int]] = [set() for _ in range(n)]
    keys = [_primary_entity_key(str(r.get("title") or "")) for r in rels]
    id_sets = [_evidence_item_ids(r) for r in rels]

    for i in range(n):
        for j in range(i + 1, n):
            same_entity = bool(keys[i] and keys[i] == keys[j] and len(keys[i]) >= 2)
            overlap = False
            a, b = id_sets[i], id_sets[j]
            if a and b:
                inter = len(a & b)
                union = len(a | b) or 1
                overlap = (inter / union) >= 0.5 or inter >= 2
            if same_entity or overlap:
                peers[i].add(j)
                peers[j].add(i)

    for i, r in enumerate(rels):
        if not peers[i]:
            r.pop("_draft_warning", None)
            r.pop("_dup_peer_titles", None)
            continue
        peer_titles = [
            (rels[j].get("title") or "").strip() or "（无标题）"
            for j in sorted(peers[i])
        ]
        r["_draft_warning"] = "suspected_duplicate"
        r["_dup_peer_titles"] = peer_titles[:6]
        r["needs_review"] = True
    return rels


def items_for_issue(con, issue_id: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """SELECT id, source_id, owner_team, pointer, entities, blocked, text
           FROM items WHERE issue_id=?""",
        (issue_id,),
    ).fetchall()
    return [dict(r) for r in rows]
