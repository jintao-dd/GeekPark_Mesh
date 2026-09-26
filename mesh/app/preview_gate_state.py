"""预览门禁状态：改稿后作废 _preview_gate_ok。"""
from __future__ import annotations


def clear_preview_gate(data: dict) -> dict:
    """稿面被改动后作废进预览检查标记，上线须重新「生成预览」。"""
    if not isinstance(data, dict):
        return data
    had = bool(
        data.get("_preview_gate_ok")
        or data.get("_preview_gate_at")
        or data.get("_preview_gate_stale")
    )
    data.pop("_preview_gate_ok", None)
    data.pop("_preview_gate_at", None)
    if had:
        data["_preview_gate_stale"] = True
    return data


def edit_invalidates_preview_gate(path: str) -> bool:
    """改叙事/关系/KPI 等读者可见字段即作废 gate。"""
    head = (path or "").split(".", 1)[0].strip()
    return head in {
        "question", "lead", "kpis", "relations", "contacts", "keywords",
        "plans", "views", "gaps", "data_sources",
    }
