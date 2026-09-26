"""Ask 答案 citation 硬校验：实体×团队归属必须能在 contexts 中溯源。"""
from __future__ import annotations

import re
from typing import Any

from . import ingest, qa_structured
from .aggregator import sanitize_owner_team
from .owner_guard import _title_entities

_SENT_SPLIT = re.compile(r"(?<=[。！？\n])|(?<=[.!?])\s+")
_DISCLAIMER = "（注：部分表述因可用记录中缺少对应依据，已省略。）"

# 归属关系 cue：只有句子出现这些模式，才把「实体 × 团队」视为归属主张
_OWNERSHIP_CUES = [
    re.compile(r"(.{2,30}?)的\s*([^的\s]{2,20})\s*(?:团队|部门|组)"),
    re.compile(r"([^\s]{2,20}?)\s*(?:团队|部门|组)\s*的\s*(.{2,30})"),
    re.compile(r"(.{2,30}?)\s*(?:来自|属于|归属于|在|加入|调入|转至|调到)\s*([^\s]{2,20})"),
    re.compile(r"([^\s]{2,20}?)\s*(?:团队|部门|组)\s*(?:有|提到|记录|涉及|包含|负责)\s*(.{2,30})"),
    # 团队主动与某主体发生动作（如「BD 团队与张三讨论」）
    re.compile(r"([^\s]{2,20}?)\s*(?:团队|部门|组)?\s*(?:与|和|同|跟)\s*(.{2,30})\s*(?:讨论|沟通|交流|会面|接洽|合作|推进|协商|洽谈)"),
]


def _is_explicit_team_assignment(sentence: str) -> bool:
    """判断是否出现明确的「实体归属团队」表达。"""
    for pat in _OWNERSHIP_CUES:
        if pat.search(sentence):
            return True
    return False


def _ctx_team(ctx: dict) -> str:
    for key in ("归属团队", "团队", "owner_team"):
        t = sanitize_owner_team(ctx.get(key))
        if t:
            return t
    return ""


def _ctx_blob(ctx: dict) -> str:
    parts = [
        str(ctx.get("期号") or ctx.get("issue") or ""),
        str(ctx.get("章节") or ctx.get("section") or ""),
        str(ctx.get("标题") or ctx.get("title") or ""),
        str(ctx.get("内容") or ctx.get("body") or ""),
        str(ctx.get("归属团队") or ctx.get("团队") or ""),
    ]
    return " ".join(parts)


def contexts_evidence(contexts: list[dict] | None) -> dict[str, Any]:
    """从 Ask contexts 建证据索引（含 item_id / chunk_id 映射）。"""
    blob_parts: list[str] = []
    teams: set[str] = set()
    entity_teams: dict[str, set[str]] = {}
    entities: set[str] = set()
    by_item: dict[str, dict] = {}
    by_chunk: dict[str, dict] = {}

    for ctx in contexts or []:
        if not isinstance(ctx, dict):
            continue
        sec = str(ctx.get("章节") or ctx.get("section") or "")
        if sec in ("检索范围", "查询说明"):
            continue
        text = _ctx_blob(ctx)
        blob_parts.append(text)
        team = _ctx_team(ctx)
        if team:
            teams.add(team)
        title = str(ctx.get("标题") or ctx.get("title") or "")
        iid = ctx.get("条目ID") or ctx.get("item_id")
        cid = ctx.get("chunk_id") or ctx.get("chunkId")
        row = {"blob": text, "team": team, "title": title, "item_id": iid, "chunk_id": cid}
        if iid is not None and str(iid).strip():
            by_item[str(iid)] = row
        if cid:
            by_chunk[str(cid)] = row
        for name in _title_entities(title):
            if len(name) < 2:
                continue
            entities.add(name)
            if team:
                entity_teams.setdefault(name.lower(), set()).add(team)

    blob = "\n".join(blob_parts)
    return {
        "blob": blob,
        "blob_l": blob.lower(),
        "teams": teams,
        "entities": entities,
        "entity_teams": entity_teams,
        "by_item": by_item,
        "by_chunk": by_chunk,
    }


def _entity_in_blob(name: str, blob_l: str) -> bool:
    return bool(name) and name.lower() in blob_l


def _claimed_entity_team_pairs(sentence: str, known_entities: set[str]) -> list[tuple[str, str]]:
    """
    识别句子中明确的「实体 × 团队」归属主张。
    仅当句子出现明确归属 cue 时才判定，避免把普通共现（如列表、并列）误判为归属。
    """
    teams = qa_structured.find_teams_in_question(sentence)
    if not teams:
        return []
    # 没有明确归属 cue 时，不生成需要校验的实体×团队对
    if not _is_explicit_team_assignment(sentence):
        return []
    found: list[tuple[str, str]] = []
    sl = sentence.lower()
    for name in known_entities:
        if len(name) < 2:
            continue
        if name.lower() not in sl:
            continue
        for t in teams:
            found.append((name, t))
    return found


def _attribution_ok(name: str, team: str, evidence: dict) -> bool:
    if not _entity_in_blob(name, evidence["blob_l"]):
        return False
    et = evidence["entity_teams"].get(name.lower()) or set()
    if et:
        return team in et
    # 无结构化归属时：仅允许「同一证据行」共现，且该行本身须含实体名
    for line in evidence["blob"].split("\n"):
        ll = line.lower()
        if name.lower() not in ll:
            continue
        if team in qa_structured.find_teams_in_question(line):
            return True
    return False


def _best_evidence_binding(sentence: str, evidence: dict) -> dict[str, list]:
    """句级 claim → item_id/chunk_id 映射（后端证据链）。"""
    item_ids: list[str] = []
    chunk_ids: list[str] = []
    s = re.sub(r"\s+", "", sentence)
    best_score, best_iid, best_cid = 0.0, None, None

    def _grams(x: str) -> set[str]:
        x = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", x)
        return {x[i : i + 2] for i in range(max(0, len(x) - 1))}

    sg = _grams(s)
    for iid, row in (evidence.get("by_item") or {}).items():
        blob = str(row.get("blob") or "")
        if not blob or not sg:
            continue
        bg = _grams(blob)
        score = len(sg & bg) / len(sg) if sg else 0
        if score > best_score:
            best_score, best_iid, best_cid = score, iid, row.get("chunk_id")
    if best_score >= 0.18:
        if best_iid:
            item_ids.append(str(best_iid))
        if best_cid:
            chunk_ids.append(str(best_cid))
    return {"item_ids": item_ids, "chunk_ids": chunk_ids}


def validate_answer(answer: str, contexts: list[dict] | None) -> dict[str, Any]:
    """
    校验答案：去掉「实体×团队」无法在 contexts 溯源的句子。
    返回 {answer, flags, removed}。
    """
    text = (answer or "").strip()
    if not text:
        return {"answer": text, "flags": [], "removed": []}

    evidence = contexts_evidence(contexts)
    if not evidence["blob"].strip():
        return {"answer": text, "flags": [], "removed": []}

    source_block = ""
    m = re.search(r"\n来源[：:]", text)
    body = text
    if m:
        body = text[: m.start()].rstrip()
        source_block = text[m.start() :].strip()

    known = set(evidence["entities"])
    for m2 in re.finditer(r"[\u4e00-\u9fff]{2,12}", body):
        cand = m2.group(0)
        if _entity_in_blob(cand, evidence["blob_l"]) and cand not in ingest.TEAMS:
            known.add(cand)

    kept: list[str] = []
    removed: list[str] = []
    flags: list[str] = []
    bindings: list[dict] = []

    parts = [p for p in _SENT_SPLIT.split(body) if p and p.strip()]
    if not parts:
        parts = [body]

    for sent in parts:
        s = sent.strip()
        if not s:
            continue
        bad_pairs = [
            (name, team)
            for name, team in _claimed_entity_team_pairs(s, known)
            if not _attribution_ok(name, team, evidence)
        ]
        if bad_pairs:
            removed.append(s)
            for name, team in bad_pairs:
                flags.append(f"{name}×{team}")
            continue
        bind = _best_evidence_binding(s, evidence)
        bindings.append({"sentence": s[:200], **bind})
        kept.append(sent)

    new_body = "".join(kept).strip()
    if removed and not new_body:
        new_body = "可用记录中未找到足以支持该回答的出处。"

    out = new_body
    if source_block:
        out = f"{new_body}\n\n{source_block}" if new_body else source_block
    if removed:
        out = f"{out.rstrip()}\n{_DISCLAIMER}" if out else _DISCLAIMER

    return {
        "answer": out.strip(),
        "flags": list(dict.fromkeys(flags)),
        "removed": removed,
        "bindings": bindings,
    }
