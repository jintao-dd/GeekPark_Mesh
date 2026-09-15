"""名单型客户条目拆分：把「沟通中：A、B、C」炸成一主体一条。

只改形态、不改安全边界：父条已 blocked / zone=5 / L3 的不拆；
拆出的子条仍走 zone_hard.apply_hard_blocks，命中⑤区照常拦截。
禁止在本模块改写或丢弃⑤区语义。
"""
from __future__ import annotations

import re
from typing import Any

# 与 extract_T6 阶段词对齐
_STAGE_WORDS = (
    "方案待确认",
    "已签约执行中",
    "合同流程中",
    "已完成",
    "接触中",
    "沟通中",
    "暂停",
)
_STAGE_ALT = "|".join(re.escape(s) for s in _STAGE_WORDS)
# 「沟通中：A、B」或「接触中: A, B」
_LIST_HEAD = re.compile(
    rf"^\s*(?P<stage>{_STAGE_ALT})\s*[:：]\s*(?P<body>.+?)\s*$",
    re.DOTALL,
)
# 按顿号/逗号切主体，括号内逗号不切开
_SPLIT_TOP = re.compile(r"[、,，](?![^（(]*[）)])")
# 主体 + 可选括号说明：比亚迪（30 秒简讯视频）
_SUBJECT_NOTE = re.compile(
    r"^\s*(?P<name>[^（(]+?)\s*(?P<note>[（(].+[）)])?\s*$"
)


def _is_blocked_parent(it: dict[str, Any]) -> bool:
    if int(it.get("blocked") or 0) == 1:
        return True
    if int(it.get("zone") or 0) == 5:
        return True
    if str(it.get("level") or "").upper() == "L3":
        return True
    return False


def _split_subjects(body: str) -> list[tuple[str, str]]:
    """返回 [(subject, note_without_parens_or_empty), ...]。"""
    parts = [p.strip() for p in _SPLIT_TOP.split(body or "") if p.strip()]
    if len(parts) < 2:
        return []
    out: list[tuple[str, str]] = []
    for p in parts:
        m = _SUBJECT_NOTE.match(p)
        if not m:
            continue
        name = (m.group("name") or "").strip()
        note_raw = (m.group("note") or "").strip()
        note = note_raw.strip("（()）").strip() if note_raw else ""
        if name:
            out.append((name, note))
    return out if len(out) >= 2 else []


def _child_text(name: str, stage: str, note: str) -> str:
    if note:
        return f"{name}：{stage}，{note}"
    return f"{name}：{stage}"


def _filter_entities(entities: Any, name: str) -> list:
    ents = list(entities) if isinstance(entities, list) else []
    keep = [e for e in ents if str(e).strip() and (str(e).strip() in name or name in str(e).strip())]
    return keep if keep else [name]


def _signals_with_split(signals: Any) -> list:
    sigs = list(signals) if isinstance(signals, list) else []
    if "stage_list_split" not in sigs:
        sigs = list(sigs) + ["stage_list_split"]
    return sigs


def maybe_split_stage_list_item(it: dict[str, Any]) -> list[dict[str, Any]]:
    """若是阶段名单行则拆成多条；否则返回单元素列表。"""
    if _is_blocked_parent(it):
        return [it]
    text = str(it.get("text") or "").strip()
    m = _LIST_HEAD.match(text)
    if not m:
        return [it]
    stage = m.group("stage")
    subjects = _split_subjects(m.group("body") or "")
    if len(subjects) < 2:
        return [it]

    children: list[dict[str, Any]] = []
    for name, note in subjects:
        child = dict(it)
        child["text"] = _child_text(name, stage, note)
        child["entities"] = _filter_entities(it.get("entities"), name)
        child["signals"] = _signals_with_split(it.get("signals"))
        # 不继承父级「已拦」以外的假安全：子条默认未拦，交给 zone_hard
        child["blocked"] = 0
        children.append(child)
    return children


def expand_stage_list_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对抽取结果做名单拆分（安全父条跳过）。"""
    out: list[dict[str, Any]] = []
    for it in items:
        out.extend(maybe_split_stage_list_item(it if isinstance(it, dict) else {}))
    return out
