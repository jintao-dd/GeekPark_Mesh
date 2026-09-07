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
    """条目+要点卡指纹：未变则可跳过周报壳与关系 LLM。"""
    blob = "\n".join(
        [
            "teams:" + ",".join(teams or []),
            "items:" + "|".join(item_fps or []),
            "cards:" + "|".join(card_fps or []),
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def attach_content_fingerprint(data: dict, fp: str) -> dict:
    out = dict(data) if isinstance(data, dict) else {}
    out["_preview_content_fp"] = fp
    return out


def content_fingerprint_matches(data: dict | None, fp: str) -> bool:
    if not fp or not isinstance(data, dict):
        return False
    return str(data.get("_preview_content_fp") or "").strip() == fp
