"""Phase 3 Verify v2：周报 draft 各 LLM 区块必须 grounded 到 items / relation evidence。"""
from __future__ import annotations

import json
import re
from typing import Any

from . import qa_structured
from .relation_verify import _key_tokens, line_grounded

# 元数据行不参与事实校验
_META_ROW_KEYS = frozenset({"来源", "时间", "身份", "cert", "sub"})


def _parse_entities(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def build_draft_evidence_corpus(
    items: list[dict],
    relations: list[dict] | None = None,
    team_cards: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """material → item/chunk 语料；relations 已挂载的 evidence[] 一并纳入。"""
    entries: list[dict] = []
    parts: list[str] = []

    def _add(blob: str, *, item_id=None, source_id=None, team="", pointer="", source_label=""):
        s = (blob or "").strip()
        if not s:
            return
        parts.append(s)
        entries.append({
            "item_id": item_id,
            "source_id": source_id,
            "team": team,
            "snippet": s[:240],
            "pointer": pointer,
            "source_label": source_label,
        })

    for it in items or []:
        if it.get("blocked"):
            continue
        text = (it.get("text") or "").strip()
        team = (it.get("owner_team") or it.get("team") or "").strip()
        label = (it.get("source_label") or "").strip()
        ptr = (it.get("pointer") or "").strip()
        iid = it.get("id")
        sid = it.get("source_id")
        if text:
            _add(text, item_id=iid, source_id=sid, team=team, pointer=ptr, source_label=label)
        for ent in _parse_entities(it.get("entities")):
            _add(ent, item_id=iid, source_id=sid, team=team, pointer=ptr, source_label=label)

    for rel in relations or []:
        title = (rel.get("title") or "").strip()
        if title:
            _add(title)
        for e in rel.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            snip = (e.get("snippet") or e.get("quote") or "").strip()
            if snip:
                _add(
                    snip,
                    item_id=e.get("item_id"),
                    source_id=e.get("source_id"),
                    team=(e.get("team") or "").strip(),
                    pointer=(e.get("pointer") or "").strip(),
                    source_label=(e.get("source_label") or "").strip(),
                )

    for card in team_cards or []:
        if not isinstance(card, dict):
            continue
        for key in ("summary", "body", "title", "label"):
            _add(str(card.get(key) or ""))
        for block in card.get("blocks") or card.get("sections") or []:
            if isinstance(block, dict):
                _add(str(block.get("text") or block.get("body") or ""))
                _add(str(block.get("title") or ""))

    corpus = "\n".join(parts)
    return corpus, entries


def _allowed_teams_from_corpus(corpus: str, entries: list[dict]) -> set[str]:
    teams: set[str] = set()
    for e in entries:
        t = (e.get("team") or "").strip()
        if t:
            teams.add(t)
    for t in qa_structured.find_teams_in_question(corpus):
        teams.add(t)
    return teams


def line_grounded_in_corpus(
    line: str,
    corpus: str,
    *,
    allowed_teams: set[str] | None = None,
    min_ratio: float = 0.34,
) -> bool:
    s = (line or "").strip()
    if not s or len(s) < 4:
        return True
    if allowed_teams is not None:
        for t in qa_structured.find_teams_in_question(s):
            if t not in allowed_teams:
                return False
    tokens = _key_tokens(s)
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in corpus)
    return hits / len(tokens) >= min_ratio


def _team_has_corpus(corpus: str, team: str) -> bool:
    t = (team or "").strip()
    if not t:
        return False
    return t in corpus or f"{t}记录" in corpus or f"{t}：" in corpus


def _verify_row_value(row: dict, corpus: str, allowed_teams: set[str]) -> bool:
    k = (row.get("k") or "").strip()
    v = (row.get("v") or "").strip()
    if not v:
        return True
    if k in _META_ROW_KEYS:
        return True
    if k in allowed_teams and not _team_has_corpus(corpus, k):
        return False
    return line_grounded_in_corpus(v, corpus, allowed_teams=allowed_teams, min_ratio=0.32)


def _verify_grouped_section(
    section: dict | None,
    corpus: str,
    allowed_teams: set[str],
) -> tuple[dict, int]:
    """keywords / plans 等 groups[].items[].rows[].v"""
    if not section or not isinstance(section, dict):
        return section or {}, 0
    data = dict(section)
    trimmed = 0
    groups_out = []
    for g in data.get("groups") or []:
        if not isinstance(g, dict):
            continue
        g2 = dict(g)
        items_out = []
        for item in g.get("items") or []:
            if not isinstance(item, dict):
                continue
            it2 = dict(item)
            rows = []
            for row in item.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                if _verify_row_value(row, corpus, allowed_teams):
                    rows.append(row)
                else:
                    trimmed += 1
            it2["rows"] = rows
            name = (it2.get("name") or "").strip()
            if rows or not name:
                items_out.append(it2)
            else:
                trimmed += 1
        g2["items"] = items_out
        if items_out:
            groups_out.append(g2)
    data["groups"] = groups_out
    return data, trimmed


def _verify_contacts(contacts: list, corpus: str, allowed_teams: set[str]) -> tuple[list, int]:
    trimmed = 0
    out = []
    for block in contacts or []:
        if not isinstance(block, dict):
            continue
        b2 = dict(block)
        groups_out = []
        for g in block.get("groups") or []:
            if not isinstance(g, dict):
                continue
            g2 = dict(g)
            items_out = []
            for item in g.get("items") or []:
                if not isinstance(item, dict):
                    continue
                it2 = dict(item)
                rows = []
                for row in item.get("rows") or []:
                    if not isinstance(row, dict):
                        continue
                    k = (row.get("k") or "").strip()
                    if k in _META_ROW_KEYS:
                        rows.append(row)
                        continue
                    if _verify_row_value(row, corpus, allowed_teams):
                        rows.append(row)
                    else:
                        trimmed += 1
                it2["rows"] = rows
                if rows:
                    items_out.append(it2)
                else:
                    trimmed += 1
            g2["items"] = items_out
            if items_out:
                groups_out.append(g2)
        b2["groups"] = groups_out
        if groups_out:
            out.append(b2)
    return out, trimmed


def _verify_views(views: list, corpus: str, allowed_teams: set[str]) -> tuple[list, int]:
    trimmed = 0
    out = []
    for v in views or []:
        if not isinstance(v, dict):
            continue
        text = (v.get("text") or "").strip()
        if not text:
            continue
        if line_grounded_in_corpus(text, corpus, allowed_teams=allowed_teams, min_ratio=0.28):
            out.append(v)
        else:
            trimmed += 1
            topic = (v.get("topic") or "本期观察").strip()
            src = (v.get("source") or "").strip()
            skeleton = f"（以下表述因缺少条目依据已省略；主题：{topic}）"
            if src and line_grounded_in_corpus(src, corpus, allowed_teams=allowed_teams, min_ratio=0.2):
                out.append({"topic": topic, "text": skeleton, "source": src, "weak": True})
    return out, trimmed


def _verify_lead(lead: str, corpus: str, allowed_teams: set[str]) -> tuple[str, bool]:
    s = (lead or "").strip()
    if not s:
        return s, True
    sentences = re.split(r"(?<=[。！？])\s*", s)
    kept = [x for x in sentences if line_grounded_in_corpus(x, corpus, allowed_teams=allowed_teams, min_ratio=0.26)]
    if kept:
        return "".join(kept), len(kept) == len(sentences)
    teams = "、".join(sorted(allowed_teams)[:6]) or "各团队"
    return f"本期由{teams}的记录汇成（导语中部分表述因缺少条目依据已省略）。", False


def verify_issue_draft(
    draft: dict,
    items: list[dict],
    *,
    team_cards: list[dict] | None = None,
) -> dict:
    """删减/降级 draft 中无法由 items+evidence 证明的 LLM narrative。不修改 pipeline 步骤。"""
    data = dict(draft)
    relations = list(data.get("relations") or [])
    corpus, _entries = build_draft_evidence_corpus(items, relations, team_cards)
    allowed_teams = _allowed_teams_from_corpus(corpus, _entries)

    meta = dict(data.get("_verify") or {})
    lead, lead_ok = _verify_lead(data.get("lead") or "", corpus, allowed_teams)
    data["lead"] = lead
    meta["lead_ok"] = lead_ok

    kw, kw_trim = _verify_grouped_section(data.get("keywords"), corpus, allowed_teams)
    data["keywords"] = kw
    meta["keywords_rows_trimmed"] = kw_trim

    pl, pl_trim = _verify_grouped_section(data.get("plans"), corpus, allowed_teams)
    data["plans"] = pl
    meta["plans_rows_trimmed"] = pl_trim

    contacts, c_trim = _verify_contacts(data.get("contacts") or [], corpus, allowed_teams)
    data["contacts"] = contacts
    meta["contacts_rows_trimmed"] = c_trim

    views, v_trim = _verify_views(data.get("views") or [], corpus, allowed_teams)
    data["views"] = views
    meta["views_trimmed"] = v_trim

    meta["corpus_chars"] = len(corpus)
    data["_verify"] = meta
    return sync_kpis_from_data(data)


def _items_from_groups(section: dict | None) -> list[dict]:
    if not section:
        return []
    out: list[dict] = []
    for g in section.get("groups") or []:
        for it in g.get("items") or []:
            if (it.get("name") or "").strip():
                out.append(it)
    return out


def _contact_item_names(contacts: list) -> list[str]:
    names: list[str] = []
    for c in contacts or []:
        for g in c.get("groups") or []:
            for it in g.get("items") or []:
                n = (it.get("name") or "").strip()
                if n and n not in names:
                    names.append(n)
    return names


def _founder_dialogue_count(contacts: list) -> int:
    n = 0
    for c in contacts or []:
        head = f"{c.get('label') or ''} {c.get('title') or ''}"
        if not any(x in head for x in ("一手对话", "创始人与高管", "创始人与")):
            continue
        for g in c.get("groups") or []:
            n += len([it for it in (g.get("items") or []) if (it.get("name") or "").strip()])
    return n


def _format_kpi_n(n: int, *, use_plus: bool = False) -> str:
    if n <= 0:
        return "0"
    if use_plus and n >= 40:
        return f"{n}+"
    return str(n)


def sync_kpis_from_data(data: dict) -> dict:
    """首屏 KPI 与下方卡片数量对齐（merge/verify 后以实际数据为准）。

    「可同步的关系」= 读者可见 strong 卡数（与 build_published_projection / 读者区一致），
    不含 parallel/watch 草稿积压。
    """
    from .relation_display import count_reader_relations

    data = dict(data)
    relations = list(data.get("relations") or [])
    contacts = list(data.get("contacts") or [])
    contact_names = _contact_item_names(contacts)
    kw_items = _items_from_groups(data.get("keywords"))
    founder_n = _founder_dialogue_count(contacts)
    data["kpis"] = [
        {"n": _format_kpi_n(count_reader_relations(relations)), "label": "可同步的关系"},
        {"n": _format_kpi_n(len(contact_names), use_plus=True), "label": "接触过的人"},
        {"n": _format_kpi_n(founder_n), "label": "创始人级一手对话"},
        {"n": _format_kpi_n(len(kw_items)), "label": "关注的事"},
    ]
    return data


def issue_field_inventory() -> list[dict]:
    """LLM 生成字段 × 生成路径 × evidence 约束状态（Phase 3 盘点）。"""
    return [
        {"field": "relations[].body/details", "path": "llm.build_issue_draft → merge_relations_from_candidates", "evidence": "relation.evidence[] + relation_verify"},
        {"field": "relations[].evidence", "path": "relation_candidates._build_evidence", "evidence": "item_id/source_id 必填"},
        {"field": "lead", "path": "llm.build_issue_draft", "evidence": "issue_verify (items corpus)"},
        {"field": "keywords.groups[].items[].rows[].v", "path": "llm.build_issue_draft", "evidence": "issue_verify (items corpus)"},
        {"field": "plans.groups[].items[].rows[].v", "path": "llm.build_issue_draft", "evidence": "issue_verify (items corpus)"},
        {"field": "views[].text", "path": "llm.build_issue_draft", "evidence": "issue_verify (items corpus)"},
        {"field": "contacts[].groups[].items[].rows (要点等)", "path": "llm.build_issue_draft", "evidence": "issue_verify (items corpus)"},
        {"field": "kpis", "path": "sync_kpis_from_data (after merge/verify)", "evidence": "count_reader_relations + contacts/keywords"},
        {"field": "gaps", "path": "llm.build_issue_draft", "evidence": "未约束（缺口说明）"},
        {"field": "data_sources", "path": "llm.build_issue_draft", "evidence": "未约束（接入状态）"},
        {"field": "question", "path": "模板固定", "evidence": "N/A"},
    ]


def _is_verified_skeleton_body(body: str, rel: dict) -> bool:
    title = (rel.get("title") or "").strip()
    b = (body or "").strip()
    if not title or not b:
        return False
    return f"「{title}」" in b and "本期均有与" in b


def collect_unsupported_flags(draft: dict, *, hard_only: bool = False) -> list[str]:
    """发布前：汇总仍可能含 unsupported narrative 的标记。

    hard_only=True 时只返回仍可能伤害读者稿的硬问题；
    「已删减 N 处」表示 verify 已处理，不再拦上线。
    """
    flags: list[str] = []
    vmeta = draft.get("_verify") or {}
    if not hard_only:
        if vmeta.get("lead_ok") is False:
            flags.append("lead 含已删减的 unsupported 表述")
        for key, label in (
            ("keywords_rows_trimmed", "keywords"),
            ("plans_rows_trimmed", "plans"),
            ("contacts_rows_trimmed", "contacts"),
            ("views_trimmed", "views"),
        ):
            n = int(vmeta.get(key) or 0)
            if n:
                flags.append(f"{label} 已删减 {n} 处 unsupported 行")

    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "（无标题）").strip()
        if not r.get("evidence") and not r.get("weak"):
            flags.append(f"关系「{title}」无 evidence 且未标 weak")
        if r.get("needs_review"):
            continue
        if r.get("status") == "weak" or r.get("weak"):
            continue
        body = (r.get("body") or "").strip()
        if body and not _is_verified_skeleton_body(body, r) and not line_grounded(body, r):
            flags.append(f"关系「{title}」body 无法由 evidence 证明")
        for d in r.get("details") or []:
            if d and not line_grounded(str(d), r):
                flags.append(f"关系「{title}」detail unsupported")
                break
    return flags
