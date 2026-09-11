"""受控多步：群成员 + 多人 freebusy → 候选时段（非自由 ReAct）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..tool_contract import ToolResultEnvelope
from . import backends, flags
from .normalize import envelope_fail, envelope_ok, normalize_docs

_TZ = timezone(timedelta(hours=8))


def propose_meeting(
    *,
    chat_id: str,
    days: int = 5,
    duration_min: int = 60,
    identity: Any = None,
    user_access_token: str = "",
    max_slots: int = 5,
) -> ToolResultEnvelope:
    if not flags.hands_enabled():
        return envelope_fail("hands_disabled", tool="feishu.calendar.propose")
    cid = (chat_id or "").strip()
    if not cid:
        return envelope_fail("chat_id_required_for_propose", tool="feishu.calendar.propose")

    open_id = ""
    if identity is not None:
        open_id = str(getattr(identity, "feishu_open_id", None) or "").strip()

    # 1) members
    mem = backends.call_tool(
        "feishu.search",
        {"query": "", "resource_type": "member", "chat_id": cid, "max_results": 40},
        timeout_sec=20,
        user_access_token=user_access_token,
        open_id=open_id,
    )
    if not mem.ok:
        return envelope_fail(mem.error or "members_failed", tool="feishu.calendar.propose")
    people = []
    for it in mem.items or []:
        oid = str(it.get("id") or it.get("snippet") or "").strip()
        name = str(it.get("title") or "").strip()
        if oid.startswith("ou_"):
            people.append({"open_id": oid, "name": name or oid})
    if not people:
        return envelope_fail("no_members", tool="feishu.calendar.propose")

    # 2) freebusy per member (cap to avoid explosion)
    busy: list[tuple[datetime, datetime]] = []
    start_day = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = start_day + timedelta(days=max(1, int(days)))
    for p in people[:12]:
        env = backends.call_tool(
            "feishu.calendar.list",
            {"query": "", "days": int(days), "max_results": 50},
            timeout_sec=15,
            user_access_token=user_access_token,
            open_id=p["open_id"],
        )
        if not env.ok:
            continue
        for it in env.items or []:
            snip = str(it.get("snippet") or "")
            # "start ~ end"
            if " ~ " not in snip:
                continue
            a, b = snip.split(" ~ ", 1)
            try:
                sa = datetime.fromisoformat(a.strip().replace("Z", "+00:00"))
                sb = datetime.fromisoformat(b.split("（")[0].strip().replace("Z", "+00:00"))
                if sa.tzinfo is None:
                    sa = sa.replace(tzinfo=_TZ)
                if sb.tzinfo is None:
                    sb = sb.replace(tzinfo=_TZ)
                busy.append((sa.astimezone(_TZ), sb.astimezone(_TZ)))
            except Exception:
                continue

    # 3) scan work hours for free slots
    dur = timedelta(minutes=max(15, int(duration_min)))
    slots: list[dict[str, Any]] = []
    day = start_day
    while day < end_day and len(slots) < int(max_slots):
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        cursor = day.replace(hour=10, minute=0)
        day_end = day.replace(hour=18, minute=0)
        while cursor + dur <= day_end and len(slots) < int(max_slots):
            slot_end = cursor + dur
            overlap = any(not (slot_end <= b0 or cursor >= b1) for b0, b1 in busy)
            if not overlap:
                slots.append(
                    {
                        "title": f"候选 {cursor.strftime('%m-%d %H:%M')}–{slot_end.strftime('%H:%M')}",
                        "snippet": (
                            f"{cursor.isoformat()} ~ {slot_end.isoformat()} · "
                            f"已对照 {len(people)} 位成员 busy（bot freebusy）"
                        ),
                        "docs_type": "calendar",
                        "id": cursor.isoformat(),
                        "url": "",
                        "start": cursor.isoformat(),
                        "end": slot_end.isoformat(),
                    }
                )
            cursor += timedelta(minutes=30)
        day += timedelta(days=1)

    meta = {
        "members": people[:20],
        "member_count": len(people),
        "busy_intervals": len(busy),
        "days": int(days),
        "duration_min": int(duration_min),
    }
    if not slots:
        return envelope_ok(
            normalize_docs(
                [
                    {
                        "title": "未找到共同空档",
                        "snippet": f"已查 {len(people)} 人、{len(busy)} 段忙碌；可换天数或缩短时长",
                        "docs_type": "calendar",
                        "url": "",
                    }
                ],
                kind="calendar",
            ),
            tool="feishu.calendar.propose",
            meta=meta,
            max_results=8,
        )
    return envelope_ok(
        normalize_docs(slots, kind="calendar"),
        tool="feishu.calendar.propose",
        meta=meta,
        max_results=max(8, int(max_slots)),
    )
