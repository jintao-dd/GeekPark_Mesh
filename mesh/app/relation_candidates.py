"""代码侧跨团队关系候选（draft 第一阶段）。"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from .aggregator import sanitize_owner_team
from .owner_guard import (
    filter_draft_relations,
    normalize_relation_team_badges,
    normalize_pointer,
    provenance_key,
    _title_entities,
    _DETAIL_TEAM,
)
from .relation_verify import editorial_weak

_MIN_ENTITY_LEN = 2
_MIN_BRIDGE_NAME_LEN = 3
_MAX_SNIPPETS = 3
_SNIP_LEN = 120

_KNOWN_TEAMS = frozenset({
    "编辑部", "商业化团队", "硅谷 BD 团队", "Global Partnership 团队", "英文站",
    "品牌创意团队", "社群", "投资团队", "音频播客团队", "视频号团队",
    "CEO / 总裁办", "CEO", "总裁办",
})
_OVERSEAS_TEAMS = frozenset({"Global Partnership 团队", "硅谷 BD 团队"})
_ROUTE_PHRASE = re.compile(
    r"用得上|可供|对照|承接|联动|采访池|嘉宾|路由|值得关注|可对齐|国内谁用|谁用得上",
)
_CARD_ACTION_HINT = re.compile(
    r"在跟进的合作|已沟通|尚未接触|对接|采访|合作|投资|路由|用得上|对照|牵线|已接触",
)
_BRIDGE_NAME_NOISE = frozenset({
    "已沟通", "尚未接触", "已约下一步", "在聊", "等", "可对接", "拟", "对方",
    "长期", "定期", "进展", "投资人", "专访", "湾区", "客户", "团队", "国内",
    "海外", "会议", "判断", "非事实", "AI", "Infra", "Tech", "Week", "Demo",
    "Lab", "Fund", "Global", "Agency", "在跟进的合作与团队", "在跟进的合作与合作方",
    "在跟进的合作与相关方",
})


def _entity_key(name: str) -> str:
    """轻归一：折空白 + casefold。不做子串/别名库。"""
    s = re.sub(r"\s+", "", (name or "").strip())
    return s.casefold()


def _parse_entities(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _item_row(it: dict) -> dict:
    return {
        "id": it.get("id"),
        "source_id": it.get("source_id"),
        "owner_team": sanitize_owner_team(it.get("owner_team")) or "",
        "pointer": normalize_pointer(it.get("pointer")),
        "text": (it.get("text") or "")[:_SNIP_LEN],
        "source_label": (it.get("source_label") or "").strip(),
        "entities": _parse_entities(it.get("entities")),
    }


def _parse_roles(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _routing_targets(owner: str, roles: list[str], text: str) -> list[str]:
    """从 roles 或带路由短语的文本解析建议承接团队。"""
    owner = sanitize_owner_team(owner) or owner
    targets: list[str] = []
    seen: set[str] = set()
    for r in roles:
        rt = sanitize_owner_team(r) or r
        if rt in _KNOWN_TEAMS and rt != owner and rt not in seen:
            targets.append(rt)
            seen.add(rt)
    if _ROUTE_PHRASE.search(text or ""):
        for team in _KNOWN_TEAMS:
            if team == owner or team in seen:
                continue
            if team in text:
                targets.append(team)
                seen.add(team)
    return targets


def _routing_title_from_row(row: dict) -> str:
    ents = row.get("entities") or []
    if ents:
        primary = ents[0]
        for co in ents[1:]:
            if co != primary and len(co) >= 2:
                return f"{primary} · {co}" if primary not in co else primary
        return primary
    ptr = (row.get("pointer") or "").strip()
    if ptr:
        return ptr
    t = (row.get("text") or "").strip()
    return t[:40] if t else "跨团队路由"


def _routing_suggest_label(owner: str, target: str) -> str:
    if owner in _OVERSEAS_TEAMS:
        return (
            "（参考：海外接触、国内可能承接，可用「海外接触，国内可能承接」"
            "或「一方接触，另一方用得上」）"
        )
    return "（参考：若仅一方有记录，可用「一方接触，另一方用得上」）"


def _provenance_ok_from_team_map(team_map: dict[str, list[dict]], teams: list[str]) -> bool:
    """用已按 entity_key 归桶的 rows 做同源校验（避免归一后 display 名对不上 provenance）。"""
    if len(teams) < 2:
        return True
    team_buckets: dict[str, set[tuple[str, str]]] = {t: set() for t in teams}
    for t in teams:
        for row in team_map.get(t) or []:
            sid = str(row.get("source_id") or "")
            pk = provenance_key(row)
            team_buckets[t].add((sid, pk))
    present = [t for t in teams if team_buckets[t]]
    if len(present) < 2:
        return True
    for i, a in enumerate(present):
        for b in present[i + 1 :]:
            shared = team_buckets[a] & team_buckets[b]
            if shared and team_buckets[a] == shared and team_buckets[b] == shared:
                return False
    return True


def _build_entity_cooccurrence_candidates(items: list[dict]) -> list[dict]:
    """entity 跨团队共现候选（原 raw 路径；索引走 _entity_key）。"""
    active = [dict(x) for x in items if not x.get("blocked")]
    # key -> team -> rows；另记 display 名
    by_entity: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    display_of: dict[str, str] = {}
    for it in active:
        ot = sanitize_owner_team(it.get("owner_team"))
        if not ot or ot == "外部媒体":
            continue
        row = _item_row(it)
        for name in row["entities"]:
            if len(name) < _MIN_ENTITY_LEN:
                continue
            key = _entity_key(name)
            if not key:
                continue
            by_entity[key][ot].append(row)
            # 偏好更长/含空格的展示名（Field AI > FieldAI）
            prev = display_of.get(key) or ""
            if len(name) > len(prev) or (len(name) == len(prev) and " " in name and " " not in prev):
                display_of[key] = name
            elif key not in display_of:
                display_of[key] = name

    raw_cands: list[dict] = []
    for key, team_map in by_entity.items():
        teams = sorted(team_map.keys())
        if len(teams) < 2:
            continue
        entity = display_of.get(key) or key
        # 优先用归桶 rows 校验；同时保留原 API（title=display）作兜底
        if not _provenance_ok_from_team_map(team_map, teams):
            continue
        all_items: list[dict] = []
        for t in teams:
            all_items.extend(team_map[t])
        item_ids = sorted({x["id"] for x in all_items if x.get("id") is not None})
        team_facts = []
        sources: list[str] = []
        for t in teams:
            snippets = []
            srcs = []
            for row in team_map[t][: _MAX_SNIPPETS * 2]:
                if row["text"] and row["text"] not in snippets:
                    snippets.append(row["text"])
                if row["source_label"] and row["source_label"] not in srcs:
                    srcs.append(row["source_label"])
                if len(snippets) >= _MAX_SNIPPETS:
                    break
            team_facts.append({
                "team": t,
                "item_ids": [x["id"] for x in team_map[t] if x.get("id")],
                "snippets": snippets[:_MAX_SNIPPETS],
                "sources": srcs[:3],
            })
            sources.extend(s for s in srcs if s not in sources)
        title = entity
        co = _coentities(all_items, entity)
        if co:
            title = f"{entity} · {co}" if entity not in co else co
        raw_cands.append({
            "title": title,
            "teams": teams,
            "weak": False,
            "candidate_kind": "cooccurrence",
            "suggested_label": _suggest_label(teams, team_facts),
            "team_facts": team_facts,
            "sources": sources[:6],
            "item_ids": item_ids,
            "provenance_ok": True,
            "entity_key": key,
        })
    return raw_cands


def _card_action_lines(card: dict) -> list[tuple[str, str]]:
    """返回 (section_title, line) actionable 行。"""
    out: list[tuple[str, str]] = []
    for sec in card.get("sections") or card.get("blocks") or []:
        if not isinstance(sec, dict):
            continue
        st = (sec.get("title") or "").strip()
        for line in sec.get("lines") or []:
            t = (line or "").strip()
            if not t:
                continue
            blob = f"{st} {t}"
            if _CARD_ACTION_HINT.search(blob):
                out.append((st, t))
    return out


def _extract_bridge_names(text: str) -> list[str]:
    """从卡面行粗抽专名；生产路径仅作候选，最终必须命中 item entities key。"""
    names: list[str] = []
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9][A-Za-z0-9+.\-]{1,}", text or ""):
        names.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]{2,12}(?:[A-Za-z0-9+.\-]{2,})?", text or ""):
        names.append(m.group(0))
    # 连续英文词拼接：Reverie + AI → Reverie AI / ReverieAI
    latin = re.findall(r"[A-Za-z][A-Za-z0-9+.\-]*", text or "")
    for i in range(len(latin)):
        for j in range(i + 2, min(i + 4, len(latin)) + 1):
            names.append(" ".join(latin[i:j]))
            names.append("".join(latin[i:j]))
    clean: list[str] = []
    seen: set[str] = set()
    for n in names:
        if n in _BRIDGE_NAME_NOISE or len(n) < _MIN_BRIDGE_NAME_LEN:
            continue
        if n.casefold() == "ai":
            continue
        k = _entity_key(n)
        if not k or k in seen or k == "ai":
            continue
        seen.add(k)
        clean.append(n)
    return clean


def _line_hits_entity_key(line: str, key: str) -> bool:
    """白名单：折叠后的行是否包含完整 entity_key（短 key 不用包含匹配）。"""
    if not key or key == "ai" or len(key) < _MIN_BRIDGE_NAME_LEN:
        return False
    folded = _entity_key(line)
    if key not in folded:
        return False
    # 短 key（3–4）易误伤，要求前后非英文数字
    if len(key) <= 4:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", folded))
    return True


def _build_card_bridge_candidates(
    items: list[dict],
    team_cards: list[dict] | None,
) -> list[dict]:
    """卡面 actionable 线索 → 他队 item 实体：弱 routing 形态桥接。"""
    if not team_cards:
        return []

    # entity_key → rows by team
    by_key: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    display_of: dict[str, str] = {}
    for it in items:
        if it.get("blocked"):
            continue
        ot = sanitize_owner_team(it.get("owner_team")) or ""
        if not ot or ot == "外部媒体":
            continue
        row = _item_row(it)
        for name in row["entities"]:
            if len(name) < _MIN_ENTITY_LEN:
                continue
            key = _entity_key(name)
            if not key or key == "ai":
                continue
            by_key[key][ot].append(row)
            prev = display_of.get(key) or ""
            if len(name) > len(prev):
                display_of[key] = name
            elif key not in display_of:
                display_of[key] = name

    if not by_key:
        return []

    buckets: dict[tuple[str, str], dict] = {}

    def _ensure_bucket(card_team: str, key: str, other_teams: list[str], snippet: str) -> dict:
        display = display_of.get(key) or key
        bkey = (card_team, key)
        if bkey not in buckets:
            buckets[bkey] = {
                "title": display,
                "teams": [card_team],
                "weak": True,
                "candidate_kind": "card_bridge",
                "routing_targets": [],
                "suggested_label": _routing_suggest_label(card_team, other_teams[0]),
                "team_facts": [{
                    "team": card_team,
                    "item_ids": [],
                    "snippets": [],
                    "sources": [f"{card_team}要点卡"],
                }],
                "sources": [f"{card_team}要点卡"],
                "item_ids": [],
                "provenance_ok": True,
                "entity_key": key,
            }
        b = buckets[bkey]
        tf0 = b["team_facts"][0]
        if snippet and snippet not in tf0["snippets"]:
            tf0["snippets"].append(snippet[:_SNIP_LEN])
        return b

    def _attach_other(b: dict, team_map: dict[str, list[dict]], other_teams: list[str]) -> None:
        for ot in other_teams:
            badge = f"→ {ot}"
            if ot not in b["routing_targets"]:
                b["routing_targets"].append(ot)
            if badge not in b["teams"]:
                b["teams"].append(badge)
            for row in team_map[ot][:_MAX_SNIPPETS]:
                iid = row.get("id")
                if iid is not None and iid not in b["item_ids"]:
                    b["item_ids"].append(iid)
                tf_ot = next((x for x in b["team_facts"] if x["team"] == ot), None)
                if tf_ot is None:
                    tf_ot = {"team": ot, "item_ids": [], "snippets": [], "sources": []}
                    b["team_facts"].append(tf_ot)
                if iid is not None and iid not in tf_ot["item_ids"]:
                    tf_ot["item_ids"].append(iid)
                if row["text"] and row["text"] not in tf_ot["snippets"]:
                    tf_ot["snippets"].append(row["text"])
                if row["source_label"] and row["source_label"] not in tf_ot["sources"]:
                    tf_ot["sources"].append(row["source_label"])
                    if row["source_label"] not in b["sources"]:
                        b["sources"].append(row["source_label"])

    for card in team_cards:
        if not isinstance(card, dict):
            continue
        card_team = sanitize_owner_team(card.get("team") or card.get("owner_team") or "") or ""
        if not card_team or card_team == "外部媒体":
            continue
        for sec_title, line in _card_action_lines(card):
            snippet = f"[{sec_title}] {line}" if sec_title else line
            hit_keys: set[str] = set()
            for raw_name in _extract_bridge_names(line):
                key = _entity_key(raw_name)
                if key in by_key:
                    hit_keys.add(key)
            for key in by_key:
                if key in hit_keys:
                    continue
                if _line_hits_entity_key(line, key):
                    hit_keys.add(key)
            for key in hit_keys:
                team_map = by_key[key]
                other_teams = sorted(t for t in team_map.keys() if t != card_team)
                if not other_teams:
                    continue
                b = _ensure_bucket(card_team, key, other_teams, snippet)
                _attach_other(b, team_map, other_teams)

    out = []
    for b in buckets.values():
        for tf in b["team_facts"]:
            tf["snippets"] = tf["snippets"][:_MAX_SNIPPETS]
            tf["sources"] = tf["sources"][:3]
        b["sources"] = b["sources"][:6]
        b["item_ids"] = sorted({i for i in b["item_ids"] if i is not None})
        out.append(b)
    return out


def _build_routing_candidates(items: list[dict]) -> list[dict]:
    """roles/文本路由：一方有记录、另一方用得上。

    同一 owner + 同一标题主体只出 **一张** 候选，多个承接方合并为多个 `→ 团队`
   （避免张岩→投资、张岩→编辑部拆成两张卡）。
    """
    buckets: dict[tuple[str, str], dict] = {}

    for it in items:
        if it.get("blocked"):
            continue
        owner = sanitize_owner_team(it.get("owner_team")) or ""
        if not owner or owner == "外部媒体":
            continue
        row = _item_row(it)
        roles = _parse_roles(it.get("roles"))
        text = (it.get("text") or "").strip()
        targets = _routing_targets(owner, roles, text)
        if not targets:
            continue
        title = _routing_title_from_row(row)
        key = (owner, title.strip().lower())
        if key not in buckets:
            buckets[key] = {
                "title": title,
                "teams": [owner],
                "weak": True,
                "candidate_kind": "routing",
                "routing_targets": [],
                "suggested_label": _routing_suggest_label(owner, targets[0]),
                "team_facts": [{
                    "team": owner,
                    "item_ids": [],
                    "snippets": [],
                    "sources": [],
                }],
                "sources": [],
                "item_ids": [],
                "provenance_ok": True,
            }
        b = buckets[key]
        for target in targets:
            badge = f"→ {target}"
            if target not in b["routing_targets"]:
                b["routing_targets"].append(target)
            if badge not in b["teams"]:
                b["teams"].append(badge)
        tf = b["team_facts"][0]
        iid = row.get("id")
        if iid is not None and iid not in tf["item_ids"]:
            tf["item_ids"].append(iid)
            b["item_ids"].append(iid)
            if row["text"] and row["text"] not in tf["snippets"]:
                tf["snippets"].append(row["text"])
            if row["source_label"] and row["source_label"] not in tf["sources"]:
                tf["sources"].append(row["source_label"])
                if row["source_label"] not in b["sources"]:
                    b["sources"].append(row["source_label"])

    out = []
    for b in buckets.values():
        b["team_facts"][0]["snippets"] = b["team_facts"][0]["snippets"][:_MAX_SNIPPETS]
        b["team_facts"][0]["sources"] = b["team_facts"][0]["sources"][:3]
        b["sources"] = b["sources"][:6]
        b["item_ids"] = sorted(set(b["item_ids"]))
        out.append(b)
    return out


def _build_raw_candidates(
    items: list[dict],
    team_cards: list[dict] | None = None,
) -> list[dict]:
    """共现 + 路由 + 卡面桥接；不在此阶段去重或截断。"""
    cooc = _build_entity_cooccurrence_candidates(items)
    route = _build_routing_candidates(items)
    bridge = _build_card_bridge_candidates(items, team_cards)
    return cooc + route + bridge


def build_relation_candidates(
    items: list[dict],
    team_cards: list[dict] | None = None,
) -> list[dict]:
    """从 items（+可选要点卡）算出跨团队候选（宽召回；去重/分级留给 Decision）。"""
    return _build_raw_candidates(items, team_cards=team_cards)


def candidate_build_stats(
    items: list[dict],
    team_cards: list[dict] | None = None,
) -> dict[str, int]:
    """候选构建各阶段数量（审计用）。"""
    cooc = _build_entity_cooccurrence_candidates(items)
    route = _build_routing_candidates(items)
    bridge = _build_card_bridge_candidates(items, team_cards)
    raw = cooc + route + bridge
    return {
        "raw": len(raw),
        "raw_cooccurrence": len(cooc),
        "raw_routing": len(route),
        "raw_card_bridge": len(bridge),
        "for_llm": len(raw),
        # 兼容旧审计字段：Candidate 不再 dedupe/cap
        "deduped": len(raw),
    }


def _coentities(items: list[dict], primary: str) -> str:
    counts: dict[str, int] = defaultdict(int)
    for row in items:
        for n in row.get("entities") or []:
            if n != primary and len(n) >= 2:
                counts[n] += 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda x: (x[1], len(x[0])))[0]


def _suggest_label(teams: list[str], team_facts: list[dict]) -> str:
    """仅作 LLM 参考 hint；不写入 relation.label（避免清一色「各知一半」）。"""
    counts = [len(tf.get("snippets") or []) for tf in team_facts]
    if not any(counts):
        return ""
    if all(c >= 1 for c in counts):
        return "（参考：若两团队事实互补、无冲突，可用「同一件事，两个部门各知一半」）"
    return "（参考：若仅一方有记录，可用「一方接触，另一方用得上」）"


_MAX_CANDIDATES_FOR_LLM = 28  # 仅诊断/历史对比；build 路径不再 cap


def _cap_candidates(cands: list[dict]) -> list[dict]:
    """诊断用：模拟旧版 28 条 cap（生产 build 路径不调用）。"""
    cands.sort(key=lambda c: (-len(c.get("item_ids") or []), -(len(c.get("teams") or [])), c.get("title") or ""))
    return cands[:_MAX_CANDIDATES_FOR_LLM]


def _dedupe_only(cands: list[dict]) -> list[dict]:
    """诊断用：模拟旧版 item 重叠去重（生产 build 路径不调用）。"""
    cands = list(cands)
    cands.sort(key=lambda c: (-len(c.get("item_ids") or []), c.get("title") or ""))
    kept: list[dict] = []
    seen_ids: list[set] = []
    seen_routing: set[tuple] = set()
    for c in cands:
        if c.get("candidate_kind") == "routing":
            solid = [t for t in (c.get("teams") or []) if not str(t).strip().startswith(("→", "->"))]
            owner = solid[0] if solid else ""
            tgts = tuple(sorted(c.get("routing_targets") or []))
            rkey = (owner, tgts, (c.get("title") or "").strip().lower())
            if rkey in seen_routing:
                continue
            seen_routing.add(rkey)
            kept.append(c)
            seen_ids.append(set(c.get("item_ids") or []))
            continue
        ids = set(c.get("item_ids") or [])
        if any(len(ids & old) / max(1, len(ids | old)) > 0.85 for old in seen_ids):
            continue
        kept.append(c)
        seen_ids.append(ids)
    return kept


def _dedupe_candidates(cands: list[dict]) -> list[dict]:
    """诊断用：dedupe + cap 旧链路（生产 build 路径不调用）。"""
    return _cap_candidates(_dedupe_only(cands))


def _title_match(a: str, b: str) -> bool:
    """宽松匹配（仅 dedupe / 诊断）；candidate 挂载用 _strict_title_match。"""
    aa = (a or "").strip().lower()
    bb = (b or "").strip().lower()
    if not aa or not bb:
        return False
    if aa == bb or aa in bb or bb in aa:
        return True
    pa = _title_entities(a)
    pb = _title_entities(b)
    return bool(pa & pb)


def _strict_title_match(a: str, b: str) -> bool:
    """严格匹配：禁止单 entity 猜测挂载（如仅「豆包」撞上另一张卡）。"""
    aa = (a or "").strip().lower()
    bb = (b or "").strip().lower()
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    pa = _title_entities(a)
    pb = _title_entities(b)
    if not pa or not pb:
        return False
    if pa == pb:
        return True
    if len(pa) == 1 or len(pb) == 1:
        return False
    sa, sb = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return bool(len(sa) < len(sb) and sa <= sb)


def _facts_detail(tf: dict) -> str:
    team = tf.get("team") or ""
    snips = tf.get("snippets") or []
    if snips:
        return f"{team}记录：{snips[0]}"
    return f"{team}记录：（见来源）"


def _items_index(items: list[dict]) -> dict:
    return {x["id"]: x for x in items if x.get("id") is not None}


def _build_evidence(cand: dict, items_by_id: dict[int, dict]) -> list[dict]:
    """从 candidate.team_facts 展开为可回溯 evidence 列表。"""
    evidence: list[dict] = []
    seen: set = set()
    for tf in cand.get("team_facts") or []:
        team = tf.get("team") or ""
        snippets = list(tf.get("snippets") or [])
        for iid in tf.get("item_ids") or []:
            if iid in seen:
                continue
            seen.add(iid)
            row = items_by_id.get(iid) or {}
            snippet = snippets.pop(0) if snippets else (row.get("text") or "")[:_SNIP_LEN]
            item_team = sanitize_owner_team(row.get("owner_team")) or team
            evidence.append({
                "item_id": iid,
                "source_id": row.get("source_id"),
                "team": item_team,
                "snippet": snippet,
                "quote": snippet,
                "source_label": row.get("source_label") or "",
                "pointer": row.get("pointer") or "",
            })
    return evidence


def _relation_type_from_rel(rel: dict, cand: dict | None = None) -> str:
    label = (rel.get("label") or (cand or {}).get("suggested_label") or "").strip()
    if "两个部门" in label or "各知一半" in label:
        return "cross_team_sync"
    if "一方接触" in label:
        return "cross_team_gap"
    return "cross_team_sync"


def _attach_relation_assets(rel: dict, cand: dict, items_by_id: dict[int, dict]) -> dict:
    """将 candidate 的结构化依据写入 relation（JSON 资产层）。"""
    rel = dict(rel)
    rel["item_ids"] = list(cand.get("item_ids") or [])
    rel["evidence"] = _build_evidence(cand, items_by_id)
    rel["provenance_ok"] = bool(cand.get("provenance_ok", True))
    rel["relation_type"] = _relation_type_from_rel(rel, cand)
    rel["confidence"] = 0.6 if _suggested_teams(rel) else 0.95
    rel.pop("_candidate_item_ids", None)
    return rel


def skeleton_relation(cand: dict) -> dict:
    """仅作 fallback；正常路径以 LLM 叙事为主。"""
    details = [_facts_detail(tf) for tf in cand.get("team_facts") or []]
    teams = list(cand.get("teams") or [])
    body = f"{'、'.join(teams)}本期均有与「{cand.get('title')}」相关的记录。"
    return {
        "label": "",
        "weak": bool(cand.get("weak")),
        "title": cand.get("title") or "",
        "body": body,
        "details": details,
        "sources": list(cand.get("sources") or []),
        "teams": teams,
    }


def _attach_title_match(a: str, b: str) -> bool:
    """挂载 candidate：先 strict，再要求至少两个 entity 交集（避免「豆包」误挂）。"""
    if _strict_title_match(a, b):
        return True
    pa = _title_entities(a)
    pb = _title_entities(b)
    if len(pa) < 2 or len(pb) < 2:
        return False
    return len(pa & pb) >= 2


def _find_candidate(
    title: str,
    candidates: list[dict],
    used: set[int],
    *,
    strict: bool = True,
) -> tuple[dict | None, int | None]:
    for i, c in enumerate(candidates):
        if i in used:
            continue
        ct = c.get("title") or ""
        if strict:
            if _strict_title_match(title, ct):
                return c, i
        elif _attach_title_match(title, ct):
            return c, i
    return None, None


def _find_candidate_for_rel(
    title: str,
    candidates: list[dict],
    used: set[int],
) -> tuple[dict | None, int | None]:
    """先 strict 匹配，再宽松匹配（双 entity 以上，避免单字猜测）。"""
    cand, ci = _find_candidate(title, candidates, used, strict=True)
    if cand is not None:
        return cand, ci
    return _find_candidate(title, candidates, used, strict=False)


def _suggested_teams(rel: dict) -> list[str]:
    out: list[str] = []
    for t in rel.get("teams") or []:
        s = str(t).strip()
        if s.startswith("→") or s.startswith("->"):
            out.append(s)
    return out


def _solid_teams(rel: dict) -> list[str]:
    out: list[str] = []
    for t in rel.get("teams") or []:
        s = str(t).strip()
        if s.startswith("→") or s.startswith("->"):
            continue
        ot = sanitize_owner_team(s.lstrip("→").lstrip("->").strip()) or s
        if ot and ot not in out:
            out.append(ot)
    return out


def _teams_from_evidence(evidence: list[dict]) -> list[str]:
    teams: list[str] = []
    seen: set[str] = set()
    for e in evidence:
        t = sanitize_owner_team(e.get("team")) or ""
        if t and t not in seen:
            teams.append(t)
            seen.add(t)
    return teams


def _sources_from_evidence(evidence: list[dict]) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for e in evidence:
        sl = (e.get("source_label") or "").strip()
        if sl and sl not in seen:
            sources.append(sl)
            seen.add(sl)
    return sources


def _details_from_evidence(evidence: list[dict], *, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for e in evidence:
        team = sanitize_owner_team(e.get("team")) or ""
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


def _align_relation_from_evidence(rel: dict, *, suggested: list[str] | None = None) -> dict:
    """事实 teams/sources 来自 evidence；保留 → 建议团队（虚线 dep）。"""
    rel = dict(rel)
    evidence = list(rel.get("evidence") or [])
    suggested = list(suggested or [])
    if not evidence:
        rel["teams"] = _solid_teams(rel) + [t for t in suggested if t not in rel.get("teams", [])]
        return rel
    allowed = set(_teams_from_evidence(evidence))
    rel["teams"] = _teams_from_evidence(evidence) + [t for t in suggested if t not in _teams_from_evidence(evidence)]
    rel["sources"] = _sources_from_evidence(evidence)
    kept: list[str] = []
    for d in rel.get("details") or []:
        m = _DETAIL_TEAM.match(str(d).strip())
        if m:
            ot = sanitize_owner_team(m.group(1).strip())
            if ot and ot in allowed:
                kept.append(d)
        else:
            kept.append(d)
    if not kept:
        kept = _details_from_evidence(evidence)
    rel["details"] = kept[:8]
    return normalize_relation_team_badges(rel, suggest_extra_solid=editorial_weak(rel))


def _facts_consistent(rel: dict) -> bool:
    evidence = list(rel.get("evidence") or [])
    if not evidence:
        return True
    ev_teams = _teams_from_evidence(evidence)
    rel_teams = _solid_teams(rel)
    if rel_teams != ev_teams:
        return False
    ev_sources = _sources_from_evidence(evidence)
    rel_sources = [str(s).strip() for s in rel.get("sources") or [] if str(s).strip()]
    return rel_sources == ev_sources


def _dedupe_relations_by_title(rels: list[dict]) -> list[dict]:
    """同标题保留一张；一方接触卡额外按主体名合并多个 → 团队。"""
    import re
    from .owner_guard import is_suggested_team_badge
    from .aggregator import sanitize_owner_team as _sot

    def _entity_key(title: str) -> str:
        t = (title or "").strip()
        m = re.match(r"^([^（(：:\s·•]{2,24})", t)
        return (m.group(1).strip().lower() if m else t[:24].lower())

    def _merge_teams(a: list, b: list) -> list[str]:
        solid: list[str] = []
        sug: list[str] = []
        for t in list(a or []) + list(b or []):
            s = str(t).strip()
            if not s:
                continue
            if is_suggested_team_badge(s):
                name = _sot(s.lstrip("→").lstrip("->").strip()) or s.lstrip("→").lstrip("->").strip()
                badge = f"→ {name}"
                if badge not in sug:
                    sug.append(badge)
            else:
                name = _sot(s) or s
                if name and name not in solid:
                    solid.append(name)
        return solid + sug

    def _is_onesided(r: dict) -> bool:
        label = r.get("label") or ""
        return "一方接触" in label or bool(r.get("weak")) or (r.get("decision_tier") == "watch")

    # 1) exact title dedupe
    by_title: dict[str, dict] = {}
    order: list[str] = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        key = (r.get("title") or "").strip().lower()
        if not key:
            continue
        if key not in by_title:
            by_title[key] = dict(r)
            order.append(key)
        else:
            cur = by_title[key]
            cur["teams"] = _merge_teams(cur.get("teams") or [], r.get("teams") or [])
            for field in ("details", "sources", "evidence"):
                acc = list(cur.get(field) or [])
                for x in r.get(field) or []:
                    if x not in acc:
                        acc.append(x)
                cur[field] = acc

    # 2) onesided entity merge (张岩→投资 + 张岩→编辑部)
    entity_map: dict[str, str] = {}  # entity -> first title key
    drop: set[str] = set()
    for key in order:
        r = by_title[key]
        if not _is_onesided(r):
            continue
        ek = _entity_key(r.get("title") or "")
        if not ek:
            continue
        if ek not in entity_map:
            entity_map[ek] = key
            continue
        primary = entity_map[ek]
        cur = by_title[primary]
        other = r
        cur["teams"] = _merge_teams(cur.get("teams") or [], other.get("teams") or [])
        for field in ("details", "sources", "evidence"):
            acc = list(cur.get(field) or [])
            for x in other.get(field) or []:
                if x not in acc:
                    acc.append(x)
            cur[field] = acc
        drop.add(key)

    return [by_title[k] for k in order if k not in drop]


def _finalize_relation_facts(rels: list[dict]) -> list[dict]:
    """有 evidence 的须 facts 一致；无 evidence 但含 → 建议团队的卡保留。"""
    out: list[dict] = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        if r.get("evidence"):
            aligned = _align_relation_from_evidence(r, suggested=_suggested_teams(r))
            if _facts_consistent(aligned):
                out.append(aligned)
        elif editorial_weak(r) or _suggested_teams(r):
            nr = normalize_relation_team_badges(
                dict(r),
                suggest_extra_solid=editorial_weak(r),
            )
            nr.setdefault("needs_review", True)
            nr["status"] = "needs_review"
            out.append(nr)
    return _dedupe_relations_by_title(out)


def merge_relations_from_candidates(
    draft: dict,
    candidates: list[dict],
    items: list[dict],
    *,
    team_cards: list[dict] | None = None,
) -> dict:
    """两阶段关系：Decision → Evidence Gate → Narrative。"""
    from .relation_decision import build_relations_two_phase

    return build_relations_two_phase(
        draft, candidates, items, team_cards=team_cards or [],
    )


def prepare_draft_bundle(con, issue_id: int, slug: str) -> dict:
    """供 main/preview_job 调用的 draft 输入包。"""
    from . import db

    merge_teams = db.draft_teams_for_issue(con, issue_id)
    item_rows = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, roles, text, source_label, blocked
               FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    team_cards = []
    for team in merge_teams:
        row = con.execute(
            "SELECT card_json FROM cards WHERE issue_id=? AND team=? AND status='approved'",
            (issue_id, team),
        ).fetchone()
        if row and row["card_json"]:
            try:
                card = json.loads(row["card_json"])
                if isinstance(card, dict):
                    if not (card.get("team") or card.get("owner_team")):
                        card["team"] = team
                    team_cards.append(card)
            except json.JSONDecodeError:
                pass
    external = [
        dict(x)
        for x in con.execute(
            """SELECT owner_team AS team, stype, zone, level, kind, text, entities,
                      source_label, source_labels, channel
               FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL AND stype='T7'""",
            (issue_id,),
        )
    ]
    names = []
    for x in item_rows:
        for n in _parse_entities(x.get("entities")):
            e = con.execute("SELECT first_issue FROM entities WHERE name=?", (n,)).fetchone()
            if not e or e["first_issue"] == slug:
                names.append(n)
    names = list(dict.fromkeys(names))[:40]
    from .relation_continue import annotate_candidates_with_continuity, load_recent_relation_fingerprints

    candidates = build_relation_candidates(item_rows, team_cards=team_cards)
    prior = load_recent_relation_fingerprints(con, before_slug=slug, limit_issues=8)
    annotate_candidates_with_continuity(candidates, prior)
    return {
        "team_cards": team_cards,
        "relation_candidates": candidates,
        "item_rows": item_rows,
        "external_items": external,
        "first_names": names,
        "continued_relations": sum(1 for c in candidates if isinstance(c, dict) and c.get("continued_from")),
    }
