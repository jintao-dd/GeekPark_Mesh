"""一键生成预览：要点卡 + 草稿的后台任务（可轮询进度，避免长请求超时）。

状态经 job_store 落库，多 uvicorn worker 可共享进度。
LLM 调用不持有 write_lock / 长连接，并走全局 llm_slot。
"""
from __future__ import annotations
import datetime
import json
import traceback

from . import ask_concurrency, db, llm, merge, job_store

KIND = "preview"


def _defaults(slug: str) -> dict:
    return {
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


def _new_state(slug: str) -> dict:
    st = _defaults(slug)
    st["running"] = True
    st["phase"] = "start"
    st["message"] = "准备中…"
    return st


def get_state(slug: str) -> dict:
    return job_store.get(KIND, slug, _defaults(slug))


def _set(slug: str, **kw) -> None:
    st = job_store.get(KIND, slug, _new_state(slug))
    st.update(kw)
    msg = kw.get("message")
    if msg:
        log = list(st.get("log") or [])
        log.append(msg)
        st["log"] = log[-40:]
    job_store.put(KIND, slug, st)


def _is_current(slug: str, token: int) -> bool:
    return job_store.is_current(KIND, slug, token, _defaults(slug))


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
    """启动预览生成。force=True 时取消卡住任务并重新开跑。

    status=published 时拒绝：禁止 Preview 覆盖 published_json / 正式 Ask 索引。
    需继续编辑时先走 create_revision（回到 draft）再 Preview。
    """
    from . import job_runtime

    con = db.connect()
    try:
        row = con.execute("SELECT status FROM issues WHERE slug=?", (slug,)).fetchone()
    finally:
        con.close()
    if row and (row["status"] or "") == "published":
        st = _defaults(slug)
        st["running"] = False
        st["done"] = False
        st["error"] = (
            "本期已上线，不能直接「生成预览」。"
            "请先「创建修订草稿」（回到 draft），改完后再 Preview，最后由 Owner 确认上线。"
        )
        st["error_code"] = "published_preview_forbidden"
        job_store.put(KIND, slug, st)
        return st

    st0 = _new_state(slug)
    st0["_username"] = username
    claimed = job_store.try_claim(
        KIND, slug, _defaults(slug), st0, force=force,
    )
    if not claimed:
        return get_state(slug)
    token = int(claimed.get("token") or 0)
    job_runtime.spawn_after_claim(
        KIND, slug, _defaults(slug), _run, (slug, username, token),
    )
    return get_state(slug)


def _run(slug: str, username: str, token: int = 0) -> None:
    try:
        if not _is_current(slug, token):
            return

        with db.write_lock():
            con = db.connect()
            try:
                r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
                if not r:
                    _set(slug, running=False, done=False, error="没有这一期")
                    return
                if (r["status"] or "") == "published":
                    _set(
                        slug,
                        running=False,
                        done=False,
                        error=(
                            "本期已上线，不能直接「生成预览」。"
                            "请先「创建修订草稿」再 Preview。"
                        ),
                        error_code="published_preview_forbidden",
                    )
                    return
                issue_id = r["id"]
                period_label = r["period_label"]
                date_start = r["date_start"]
                n_items = con.execute(
                    "SELECT COUNT(*) c FROM items WHERE issue_id=?", (issue_id,)
                ).fetchone()["c"]
                if not n_items:
                    _set(slug, running=False, done=False, error="请先完成挖掘与脱敏")
                    return
                unextracted = con.execute(
                    "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND length(COALESCE(text,''))>0 AND extracted=0",
                    (issue_id,),
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
                merge.apply_merge(con, issue_id)
                con.commit()

                teams = [
                    x["owner_team"]
                    for x in con.execute(
                        "SELECT DISTINCT owner_team FROM items WHERE issue_id=? AND blocked=0 "
                        "AND merged_into IS NULL AND owner_team IS NOT NULL AND owner_team<>''",
                        (issue_id,),
                    )
                    if (x["owner_team"] or "") not in ("外部媒体",)
                ]
            finally:
                con.close()

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
            with db.write_lock():
                con = db.connect()
                try:
                    items = [
                        dict(x)
                        for x in con.execute(
                            "SELECT zone, level, kind, text, entities, roles, signals, source_label, "
                            "source_labels, channel FROM items WHERE issue_id=? AND owner_team=? "
                            "AND blocked=0 AND merged_into IS NULL",
                            (issue_id, team),
                        )
                    ]
                finally:
                    con.close()

            with ask_concurrency.llm_slot(pool="job"):
                card = llm.build_team_card(team, items, period_label)

            if not _is_current(slug, token):
                return
            with db.write_lock():
                con = db.connect()
                try:
                    _upsert_team_card(con, issue_id, team, card)
                    con.commit()
                finally:
                    con.close()

        if not _is_current(slug, token):
            return

        with db.write_lock():
            con = db.connect()
            try:
                db.mark_draft_stale(con, issue_id)
                con.commit()
                from .relation_candidates import merge_relations_from_candidates, prepare_draft_bundle

                merge.apply_merge(con, issue_id)
                r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
                bundle = prepare_draft_bundle(con, issue_id, slug)
                prev = con.execute(
                    "SELECT published_json FROM issues WHERE status='published' AND date_end<? "
                    "ORDER BY date_end DESC LIMIT 1",
                    (date_start,),
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
                issue_row = dict(r)
            finally:
                con.close()

        _set(
            slug,
            phase="draft",
            cur=total,
            total=total,
            message="正在生成周报草稿…",
        )
        if not _is_current(slug, token):
            return

        with ask_concurrency.llm_slot(pool="job"):
            data = llm.build_issue_draft(
                issue_row,
                bundle["team_cards"],
                bundle["relation_candidates"],
                bundle["first_names"],
                bundle["external_items"],
                prev_summary,
            )
        data = merge_relations_from_candidates(
            data,
            bundle["relation_candidates"],
            bundle["item_rows"],
            team_cards=bundle["team_cards"],
        )
        data["slug"] = slug
        data["period_label"] = period_label
        data["version"] = issue_row.get("version")
        data.pop("_stale", None)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        if not _is_current(slug, token):
            return
        with db.write_lock():
            con = db.connect()
            try:
                payload = json.dumps(data, ensure_ascii=False)
                from .relation_display import build_published_projection
                reader_payload = json.dumps(build_published_projection(data), ensure_ascii=False)
                con.execute(
                    "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
                    (payload, reader_payload, stamp, issue_id),
                )
                db.register_entities(con, data, slug)
                db.reindex_issue(con, issue_id)
                con.execute(
                    "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
                    (issue_id, username, "prepare_preview", "", "生成要点卡与草稿，读者页已同步"),
                )
                con.commit()
            finally:
                con.close()

        _set(
            slug,
            running=False,
            done=True,
            error=None,
            phase="done",
            message="完成，读者页已更新",
            preview_url=f"/{slug}",
        )
    except Exception as e:
        traceback.print_exc()
        if not _is_current(slug, token):
            return
        msg = str(e).replace("\n", " ")[:200]
        try:
            con = db.connect()
            try:
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
            finally:
                con.close()
        except Exception:
            pass
        _set(slug, running=False, done=False, error=msg or "生成失败")
