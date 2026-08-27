"""个性化预设问题与定时推送。"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any

from . import ask_engine, llm
from .ask_scope import AskScope


def list_for_user(con, user_id: int, team: str = "") -> list[dict]:
    rows = con.execute(
        """SELECT * FROM user_ask_presets
           WHERE enabled=1 AND (
             (scope='user' AND user_id=?) OR
             (scope='team' AND target_team=?) OR
             scope='company'
           )
           ORDER BY id""",
        (user_id, team or ""),
    ).fetchall()
    return [dict(r) for r in rows]


def create_preset(
    con,
    *,
    title: str,
    question_template: str,
    scope: str = "user",
    user_id: int | None = None,
    target_team: str = "",
    schedule: str = "manual",
    schedule_time: str = "09:00",
    schedule_dow: int | None = None,
    team_scope: str = "",
    created_by: int | None = None,
) -> int:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = con.execute(
        """INSERT INTO user_ask_presets(
           scope, user_id, target_team, title, question_template, schedule,
           schedule_time, schedule_dow, team_scope, enabled, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)""",
        (
            scope, user_id, target_team or None, title, question_template, schedule,
            schedule_time, schedule_dow, team_scope or None, created_by, now, now,
        ),
    )
    return int(cur.lastrowid)


def update_preset(con, preset_id: int, **fields) -> bool:
    allowed = {
        "title", "question_template", "schedule", "schedule_time", "schedule_dow",
        "team_scope", "enabled", "scope", "target_team", "user_id",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    vals.append(preset_id)
    con.execute(f"UPDATE user_ask_presets SET {', '.join(sets)} WHERE id=?", vals)
    return True


def delete_preset(con, preset_id: int) -> None:
    con.execute("DELETE FROM user_ask_presets WHERE id=?", (preset_id,))


def render_question(preset: dict, user: dict) -> str:
    tpl = preset.get("question_template") or ""
    team = preset.get("team_scope") or user.get("team") or user.get("t") or ""
    display = user.get("display") or user.get("d") or user.get("username") or user.get("u") or ""
    today = datetime.date.today().isoformat()
    return (
        tpl.replace("{team}", team)
        .replace("{user}", display)
        .replace("{today}", today)
    )


def run_preset(con, preset: dict, user: dict) -> dict:
    q = render_question(preset, user)
    scope = AskScope(
        channel="web",
        team=preset.get("team_scope") or user.get("team") or user.get("t") or "",
        user_id=user.get("id"),
        role=user.get("role") or user.get("r") or "viewer",
        user_team=user.get("team") or user.get("t") or "",
        web_session_id=f"preset-{preset.get('id')}-u{user.get('id')}",
    )
    return ask_engine.prepare(con, q, scope)


def _due_now(preset: dict, now: datetime.datetime) -> bool:
    sched = (preset.get("schedule") or "manual").lower()
    if sched in ("manual", "on_publish", ""):
        return False
    st = (preset.get("schedule_time") or "09:00").strip()
    try:
        hh, mm = [int(x) for x in st.split(":", 1)]
    except Exception:
        hh, mm = 9, 0
    if now.hour != hh or abs(now.minute - mm) > 15:
        return False
    last = preset.get("last_pushed_at") or ""
    if last and last[:10] == now.date().isoformat():
        return False
    if sched == "weekly":
        dow = preset.get("schedule_dow")
        if dow is not None and int(dow) != now.weekday():
            return False
    return True


def run_due_pushes(con) -> int:
    """后台：到点执行 daily/weekly 预设（结果写入 push_log，飞书推送待 Bot 接入）。"""
    now = datetime.datetime.now()
    n = 0
    presets = con.execute(
        "SELECT * FROM user_ask_presets WHERE enabled=1 AND schedule IN ('daily','weekly')"
    ).fetchall()
    for p in presets:
        pd = dict(p)
        if not _due_now(pd, now):
            continue
        uid = pd.get("user_id")
        if not uid and pd.get("scope") == "team":
            users = con.execute(
                "SELECT * FROM users WHERE team=? AND role IN ('editor','viewer')",
                (pd.get("target_team") or "",),
            ).fetchall()
        elif pd.get("scope") == "company":
            users = con.execute(
                "SELECT * FROM users WHERE role IN ('viewer','editor','admin','owner')"
            ).fetchall()
        else:
            u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            users = [u] if u else []
        for u in users:
            user = dict(u)
            try:
                prepared = run_preset(con, pd, user)
                q = render_question(pd, user)
                if prepared.get("direct_answer") is not None:
                    ans = prepared["direct_answer"]
                else:
                    ans = llm.answer_question(q, prepared["contexts"], mode=prepared.get("mode", "lexical"))
                con.execute(
                    """INSERT INTO preset_push_log(preset_id, user_id, answer_snippet, ok)
                       VALUES (?,?,?,1)""",
                    (pd["id"], user["id"], (ans or "")[:500]),
                )
                n += 1
            except Exception as e:
                con.execute(
                    """INSERT INTO preset_push_log(preset_id, user_id, answer_snippet, ok, error)
                       VALUES (?,?,?,?,?)""",
                    (pd["id"], user["id"], "", 0, str(e)[:200]),
                )
        con.execute(
            "UPDATE user_ask_presets SET last_pushed_at=? WHERE id=?",
            (now.strftime("%Y-%m-%d %H:%M:%S"), pd["id"]),
        )
    return n
