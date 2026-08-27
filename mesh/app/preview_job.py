"""一键生成预览：要点卡 + 草稿的后台任务（可轮询进度，避免长请求超时）。"""
from __future__ import annotations
import datetime
import json
import threading
import traceback

from . import db, llm, merge

JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _new_state(slug: str) -> dict:
    return {
        "slug": slug,
        "running": True,
        "done": False,
        "error": None,
        "phase": "start",
        "message": "准备中…",
        "cur": 0,
        "total": 0,
        "preview_url": None,
        "log": [],
        "token": 0,
    }


def get_state(slug: str) -> dict:
    with _LOCK:
        return dict(
            JOBS.get(slug)
            or {
                "slug": slug,
                "running": False,
                "done": False,
                "error": None,
                "phase": "",
                "message": "",
                "cur": 0,
                "total": 0,
                "preview_url": None,
                "log": [],
                "token": 0,
            }
        )


def _set(slug: str, **kw) -> None:
    with _LOCK:
        st = JOBS.setdefault(slug, _new_state(slug))
        st.update(kw)
        msg = kw.get("message")
        if msg:
            log = list(st.get("log") or [])
            log.append(msg)
            st["log"] = log[-40:]


def _is_current(slug: str, token: int) -> bool:
    with _LOCK:
        st = JOBS.get(slug) or {}
        return st.get("token") == token and st.get("running")


def _upsert_team_card(con, issue_id: int, team: str, card: dict) -> None:
    payload = json.dumps(card, ensure_ascii=False)
    ex = con.execute("SELECT id FROM cards WHERE issue_id=? AND team=?", (issue_id, team)).fetchone()
    if ex:
        con.execute(
            "UPDATE cards SET card_json=?, status='approved', reviewer='mesh-auto', "
            "reviewed_at=datetime('now') WHERE id=?",
            (payload, ex["id"]),
        )
    else:
        con.execute(
            "INSERT INTO cards(issue_id,team,card_json,status,reviewer,reviewed_at) "
            "VALUES(?,?,?,'approved','mesh-auto',datetime('now'))",
            (issue_id, team, payload),
        )


def start(slug: str, username: str, *, force: bool = False) -> dict:
    """启动预览生成。force=True 时取消卡住任务并重新开跑。"""
    with _LOCK:
        cur = JOBS.get(slug)
        if cur and cur.get("running") and not force:
            return dict(cur)
        if cur and cur.get("running") and force:
            cur["running"] = False
            cur["done"] = False
            cur["error"] = "已被强制重新启动"
        token = int((cur or {}).get("token") or 0) + 1
        st = _new_state(slug)
        st["token"] = token
        JOBS[slug] = st
    threading.Thread(target=_run, args=(slug, username, token), daemon=True).start()
    return get_state(slug)


def _run(slug: str, username: str, token: int = 0) -> None:
    con = None
    try:
        if not _is_current(slug, token):
            return
        with db.write_lock():
            con = db.connect()
            r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
            if not r:
                _set(slug, running=False, done=False, error="没有这一期")
                return
            n_items = con.execute(
                "SELECT COUNT(*) c FROM items WHERE issue_id=?", (r["id"],)
            ).fetchone()["c"]
            if not n_items:
                _set(slug, running=False, done=False, error="请先完成挖掘与脱敏")
                return
            unextracted = con.execute(
                "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND extracted=0 AND length(COALESCE(text,''))>0",
                (r["id"],),
            ).fetchone()["c"]
            if unextracted:
                _set(
                    slug,
                    running=False,
                    done=False,
                    error=f"还有 {unextracted} 个来源未挖掘，请先完成挖掘",
                )
                return

            _set(slug, phase="merge", message="跨通道合并…", cur=0, total=1)
            merge.apply_merge(con, r["id"])
            con.commit()

            teams = [
                x["owner_team"]
                for x in con.execute(
                    "SELECT DISTINCT owner_team FROM items WHERE issue_id=? AND blocked=0 "
                    "AND merged_into IS NULL AND owner_team IS NOT NULL AND owner_team<>''",
                    (r["id"],),
                )
                if (x["owner_team"] or "") not in ("外部媒体",)
            ]
            total = max(1, len(teams)) + 1
            _set(slug, total=total, cur=0, phase="cards", message=f"准备生成 {len(teams)} 张要点卡…")

            for i, team in enumerate(teams):
                if not _is_current(slug, token):
                    return
                _set(
                    slug,
                    phase="cards",
                    cur=i + 1,
                    total=total,
                    message=f"要点卡 {i + 1}/{len(teams)} · {team}",
                )
                items = [
                    dict(x)
                    for x in con.execute(
                        "SELECT zone, level, kind, text, entities, roles, signals, source_label, "
                        "source_labels, channel FROM items WHERE issue_id=? AND owner_team=? "
                        "AND blocked=0 AND merged_into IS NULL",
                        (r["id"], team),
                    )
                ]
                card = llm.build_team_card(team, items, r["period_label"])
                if not _is_current(slug, token):
                    return
                _upsert_team_card(con, r["id"], team, card)
                con.commit()

            db.mark_draft_stale(con, r["id"])
            con.commit()

            if not _is_current(slug, token):
                return
            _set(
                slug,
                phase="draft",
                cur=total,
                total=total,
                message="正在生成周报草稿…",
            )
            r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
            merge.apply_merge(con, r["id"])
            teams = db.draft_teams_for_issue(con, r["id"])
            q = (
                "SELECT owner_team AS team, stype, zone, level, kind, text, entities, roles, signals, "
                "source_label, source_labels, channel FROM items WHERE issue_id=? AND blocked=0 "
                "AND merged_into IS NULL AND (owner_team IN (%s) OR stype IN ('T5','T7'))"
                % (",".join("?" * len(teams)) or "''")
            )
            rows = [dict(x) for x in con.execute(q, (r["id"], *teams))]
            internal = [x for x in rows if x["stype"] != "T7"]
            external = [x for x in rows if x["stype"] == "T7"]
            names = []
            for x in internal:
                for n in json.loads(x["entities"] or "[]"):
                    e = con.execute("SELECT first_issue FROM entities WHERE name=?", (n,)).fetchone()
                    if not e or e["first_issue"] == slug:
                        names.append(n)
            names = list(dict.fromkeys(names))[:40]
            prev = con.execute(
                "SELECT published_json FROM issues WHERE status='published' AND date_end<? "
                "ORDER BY date_end DESC LIMIT 1",
                (r["date_start"],),
            ).fetchone()
            prev_summary = ""
            if prev and prev["published_json"]:
                try:
                    pj = json.loads(prev["published_json"])
                    prev_summary = (pj.get("lead") or "") + " " + " / ".join(
                        x.get("title", "") for x in (pj.get("relations") or [])
                    )
                except Exception:
                    prev_summary = ""
            if not _is_current(slug, token):
                return
            data = llm.build_issue_draft(dict(r), internal, names, external, prev_summary)
            data["slug"] = slug
            data["period_label"] = r["period_label"]
            data["version"] = r["version"]
            data.pop("_stale", None)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            if not _is_current(slug, token):
                return
            con.execute(
                "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), stamp, r["id"]),
            )
            con.execute(
                "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
                (r["id"], username, "prepare_preview", "", "生成要点卡与草稿，进入预览"),
            )
            con.commit()
        _set(
            slug,
            running=False,
            done=True,
            error=None,
            phase="done",
            message="完成，正在进入预览…",
            preview_url=f"/{slug}?preview=1&edit=1",
        )
    except Exception as e:
        traceback.print_exc()
        if not _is_current(slug, token):
            return
        msg = str(e).replace("\n", " ")[:200]
        # 已有可用草稿时仍可进预览
        try:
            if con is None:
                con = db.connect()
            r = con.execute(
                "SELECT draft_json FROM issues WHERE slug=?", (slug,)
            ).fetchone()
            if r and db.draft_is_ready(r["draft_json"] or ""):
                _set(
                    slug,
                    running=False,
                    done=True,
                    error=None,
                    phase="done",
                    message="草稿生成异常，已用现有草稿进入预览",
                    preview_url=f"/{slug}?preview=1&edit=1&warn=1",
                )
                return
        except Exception:
            pass
        _set(slug, running=False, done=False, error=msg or "生成失败")
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
