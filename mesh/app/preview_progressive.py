"""渐进式预览：骨架稿、阶段标记、要点卡指纹复用。

产品约定
--------
1. 结构性闸门通过后即可写骨架 draft，标 ``_preview_partial_ready``，允许进预览页。
2. 后台继续：要点卡 → 周报壳 → 关系；期间 ``_preview_building=True``，``_preview_gate_ok`` 未置。
3. 全部完成后才 ``_preview_gate_ok=True`` 并清除 building；上线仍只认 gate_ok。
4. 改稿仍走 clear_preview_gate；生成中大改可能被最终落库覆盖（UI 提示）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


PHASE_CARDS = "cards"
PHASE_DRAFT = "draft"
PHASE_RELATIONS = "relations"
PHASE_DONE = "done"


def items_fingerprint(items: list[dict]) -> str:
    """团队条目内容指纹：未变则可复用已有要点卡，跳过 LLM。"""
    parts: list[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        parts.append(
            "|".join(
                [
                    str(it.get("text") or ""),
                    str(it.get("entities") or ""),
                    str(it.get("zone") or ""),
                    str(it.get("kind") or ""),
                ]
            )
        )
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def card_fingerprint(card: dict | None) -> str:
    if not isinstance(card, dict):
        return ""
    return str(card.get("_items_fp") or "").strip()


def attach_card_fingerprint(card: dict, fp: str) -> dict:
    out = dict(card) if isinstance(card, dict) else {}
    out["_items_fp"] = fp
    return out


def build_skeleton_draft(
    *,
    slug: str,
    period_label: str,
    version: Any,
    teams: list[str],
) -> dict:
    """无 LLM 的可渲染骨架，保证 issue.html 能开页。"""
    n_teams = len(teams or [])
    label = (period_label or "").strip() or "本期"
    return {
        "question": f"{label} · 沟通情报（生成中）",
        "lead": "要点卡与关系正在后台生成。可先浏览本页；完成后会自动刷新为完整草稿。",
        "kpis": [
            {"n": "0", "label": "可同步的关系"},
            {"n": str(n_teams), "label": "涉及团队"},
        ],
        "relations": [],
        "contacts": [],
        "keywords": {"groups": [], "sources": []},
        "plans": {"groups": [], "sources": []},
        "views": [],
        "gaps": [{"text": "周报壳与关系卡生成中，请稍候。"}],
        "data_sources": [],
        "slug": slug,
        "period_label": period_label,
        "version": version,
        "_preview_building": True,
        "_preview_partial_ready": True,
        "_preview_phase": PHASE_CARDS,
        "_preview_teams": list(teams or []),
        "_preview_cards_done": [],
    }


def mark_phase(data: dict, phase: str, *, cards_done: list[str] | None = None) -> dict:
    data = dict(data) if isinstance(data, dict) else {}
    data["_preview_building"] = True
    data["_preview_partial_ready"] = True
    data["_preview_phase"] = phase
    data.pop("_preview_gate_ok", None)
    data.pop("_preview_gate_at", None)
    if cards_done is not None:
        data["_preview_cards_done"] = list(cards_done)
    return data


def finalize_preview_flags(data: dict, *, stamp: str) -> dict:
    """全量通过后：开门禁，结束 building。"""
    data = dict(data) if isinstance(data, dict) else {}
    data["_preview_gate_ok"] = True
    data["_preview_gate_at"] = stamp
    data.pop("_preview_gate_stale", None)
    data.pop("_preview_building", None)
    data.pop("_preview_partial_ready", None)
    data["_preview_phase"] = PHASE_DONE
    return data


def allows_preview_entry(data: dict | None) -> bool:
    """预览页准入：完整 gate / 改稿 stale / 渐进式 partial。"""
    if not isinstance(data, dict):
        return False
    if data.get("_preview_gate_ok"):
        return True
    if data.get("_preview_gate_stale"):
        return True
    if data.get("_preview_partial_ready") or data.get("_preview_building"):
        return True
    return False


def is_building(data: dict | None) -> bool:
    return bool(isinstance(data, dict) and data.get("_preview_building"))


def load_existing_card(con, issue_id: int, team: str) -> dict | None:
    row = con.execute(
        "SELECT card_json FROM cards WHERE issue_id=? AND team=?",
        (issue_id, team),
    ).fetchone()
    if not row or not row["card_json"]:
        return None
    try:
        card = json.loads(row["card_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return card if isinstance(card, dict) else None


def preview_content_fingerprint(
    *,
    item_fps: list[str],
    card_fps: list[str],
    teams: list[str],
) -> str:
    """条目+要点卡指纹（仅用于卡复用对照；周报/关系复用见 relation_input_fingerprint）。"""
    blob = "\n".join(
        [
            "teams:" + ",".join(teams or []),
            "items:" + "|".join(item_fps or []),
            "cards:" + "|".join(card_fps or []),
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def relation_prompt_version() -> str:
    """关系 Decision/Writer + 周报壳相关 prompt 的内容指纹；prompt 一变即失效缓存。"""
    from .llm import PROMPT_DIR

    names = (
        "00_base_rules",
        "issue_relation_decisions",
        "issue_relation_writer",
        "issue_draft",
        "card_team",
    )
    parts: list[str] = []
    for name in names:
        p = PROMPT_DIR / f"{name}.md"
        try:
            parts.append(f"{name}:{hashlib.sha256(p.read_bytes()).hexdigest()[:12]}")
        except OSError:
            parts.append(f"{name}:missing")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _evidence_ids(ev) -> str:
    if not isinstance(ev, list):
        return ""
    bits: list[str] = []
    for e in ev:
        if isinstance(e, dict):
            bits.append(str(e.get("item_id") or e.get("id") or e.get("source") or e))
        else:
            bits.append(str(e))
    return ",".join(sorted(bits))


def relation_input_fingerprint(
    *,
    candidates: list[dict],
    team_cards: dict | list | None,
    item_rows: list[dict] | None = None,
    prompt_version: str | None = None,
) -> str:
    """周报壳+关系 LLM 的输入指纹。

    任一影响论证的输入变了即 invalidate：
    - candidate: teams / item_ids / evidence / 相关正文 hash
    - team_cards 结构指纹
    - prompt/version
    """
    pv = (prompt_version or relation_prompt_version()).strip()
    cand_parts: list[str] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        teams = c.get("teams") or []
        if isinstance(teams, str):
            teams = [teams]
        item_ids = c.get("item_ids") or c.get("items") or []
        if item_ids and isinstance(item_ids[0], dict):
            item_ids = [x.get("id") for x in item_ids]
        snippets = []
        for sn in (c.get("snippets") or c.get("evidence_snippets") or [])[:5]:
            if isinstance(sn, dict):
                snippets.append(str(sn.get("text") or sn.get("snippet") or "")[:80])
            else:
                snippets.append(str(sn)[:80])
        title = str(c.get("title") or c.get("entity") or c.get("label") or "")
        cand_parts.append(
            "|".join(
                [
                    str(c.get("candidate_id") or c.get("id") or title),
                    ",".join(sorted(str(t) for t in teams)),
                    ",".join(str(i) for i in sorted(item_ids, key=lambda x: str(x))),
                    _evidence_ids(c.get("evidence")),
                    title[:60],
                    hashlib.sha256("\n".join(snippets).encode("utf-8")).hexdigest()[:10],
                ]
            )
        )
    cand_parts.sort()

    card_blob = ""
    if isinstance(team_cards, dict):
        card_blob = json.dumps(team_cards, ensure_ascii=False, sort_keys=True, default=str)[:8000]
    elif isinstance(team_cards, list):
        card_blob = json.dumps(team_cards, ensure_ascii=False, sort_keys=True, default=str)[:8000]

    item_bits = []
    for it in item_rows or []:
        if not isinstance(it, dict):
            continue
        item_bits.append(
            f"{it.get('id')}|{it.get('owner_team')}|{(it.get('text') or '')[:60]}"
        )
    item_bits.sort()

    blob = "\n".join(
        [
            "pv:" + pv,
            "cands:" + "\n".join(cand_parts),
            "cards:" + hashlib.sha256(card_blob.encode("utf-8")).hexdigest()[:16],
            "items:" + hashlib.sha256("\n".join(item_bits).encode("utf-8")).hexdigest()[:16],
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:28]


def attach_content_fingerprint(data: dict, fp: str) -> dict:
    out = dict(data) if isinstance(data, dict) else {}
    out["_preview_content_fp"] = fp
    return out


def attach_relation_input_fingerprint(data: dict, fp: str) -> dict:
    out = dict(data) if isinstance(data, dict) else {}
    out["_relation_input_fp"] = fp
    out["_relation_prompt_version"] = relation_prompt_version()
    return out


def content_fingerprint_matches(data: dict | None, fp: str) -> bool:
    if not fp or not isinstance(data, dict):
        return False
    return str(data.get("_preview_content_fp") or "").strip() == fp


def relation_input_fingerprint_matches(data: dict | None, fp: str) -> bool:
    if not fp or not isinstance(data, dict):
        return False
    if str(data.get("_relation_input_fp") or "").strip() != fp:
        return False
    # prompt 文件变更即使 fp 算法漏了也挡一道
    stored_pv = str(data.get("_relation_prompt_version") or "").strip()
    if stored_pv and stored_pv != relation_prompt_version():
        return False
    return True
