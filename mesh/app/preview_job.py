"""一键生成预览：要点卡 + 草稿的后台任务（可轮询进度，避免长请求超时）。

渐进式约定（见 preview_progressive）：
- 闸门 + merge 后立刻写骨架稿并给出 preview_url（可先进预览页）
- 后台继续出卡 / 周报壳 / 关系；全部完成后才 _preview_gate_ok
- 上线仍只认 gate_ok，生成中不可发布

状态经 job_store 落库，多 uvicorn worker 可共享进度。
LLM 调用不持有 write_lock / 长连接，并走全局 llm_slot。

Cards：默认串行；MESH_PREVIEW_CARD_CONCURRENCY>1 时受控并行生成，
落库与 progress 仍按 teams 原序；gate / 输出内容语义不变。
"""
from __future__ import annotations
import datetime
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import ask_concurrency, db, llm, merge, job_store
from . import preview_progressive as prog

KIND = "preview"


def _card_concurrency() -> int:
    try:
        n = int((os.environ.get("MESH_PREVIEW_CARD_CONCURRENCY") or "1").strip() or "1")
    except ValueError:
        n = 1
    return max(1, min(8, n))


def _force_card_rebuild() -> bool:
    return (os.environ.get("MESH_PREVIEW_CARD_FORCE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


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
        "preview_ready": False,
        "log": [],
        "token": 0,
        "resilience": None,
        "final_status": None,
        "dropped_relations": None,
        "cards_done": 0,
        "cards_total": 0,
        "card_profile": None,
        "card_concurrency": 1,
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


def _preview_url(slug: str) -> str:
    return f"/{slug}?preview=1&edit=1&building=1"


def _write_partial_draft(con, issue_id: int, data: dict, stamp: str) -> None:
    """预览只写 draft_json，绝不改 published_json（已上线读者/Ask 不受影响）。"""
    payload = json.dumps(data, ensure_ascii=False)
    con.execute(
        "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
        (payload, stamp, issue_id),
    )


def start(slug: str, username: str, *, force: bool = False) -> dict:
    """启动预览生成。force=True 时取消卡住任务并重新开跑。

    已上线期也可 Preview：只写 draft_json，不改 published_json / 正式 Ask 索引；
    读者仍看线上版，Owner「确认上线」后才替换。
    """
    from . import job_runtime

    st0 = _new_state(slug)
    st0["_username"] = username
    st0["_force"] = bool(force)
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
                from .ingest import is_aggregation_source

                n_review = 0
                for row in con.execute(
                    "SELECT stype, team, channel, meta FROM sources WHERE issue_id=?",
                    (issue_id,),
                ):
                    if not is_aggregation_source(
                        stype=row["stype"] or "",
                        team=row["team"] or "",
                        channel=row["channel"] or "",
                    ):
                        continue
                    try:
                        m = json.loads(row["meta"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if (m.get("split") or {}).get("needs_review"):
                        n_review += 1
                if n_review:
                    _set(
                        slug,
                        running=False,
                        done=False,
                        error=(
                            f"有 {n_review} 个内容聚合来源拆段置信度低，请到来源页点「确认拆段归属」"
                            "或拆成单部门文件重传后再生成预览"
                        ),
                        error_code="preview_gate_blocked",
                    )
                    return
                from .attribution_verify import attribution_publish_blockers
                attr_early = attribution_publish_blockers(con, issue_id, "{}")
                if attr_early:
                    _set(
                        slug,
                        running=False,
                        done=False,
                        error="进预览前检查未通过：" + "；".join(attr_early[:8]),
                        error_code="preview_gate_blocked",
                    )
                    return
                n_noowner = con.execute(
                    "SELECT COUNT(*) c FROM items WHERE issue_id=? AND (owner_team IS NULL OR owner_team='') "
                    "AND COALESCE(blocked,0)=0 AND (merged_into IS NULL OR merged_into=0)",
                    (issue_id,),
                ).fetchone()["c"]
                if n_noowner:
                    _set(
                        slug,
                        running=False,
                        done=False,
                        error=f"还有 {n_noowner} 条条目待指定归属，不能进入预览",
                        error_code="preview_gate_blocked",
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

                # —— 渐进开门：骨架稿可渲染后即可进预览页 ——
                stamp0 = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                skeleton = prog.build_skeleton_draft(
                    slug=slug,
                    period_label=period_label or "",
                    version=r["version"],
                    teams=teams,
                )
                _write_partial_draft(con, issue_id, skeleton, stamp0)
                con.commit()
            finally:
                con.close()

        total = max(1, len(teams)) + 2  # cards + draft(+relations)
        cards_total = len(teams)
        _set(
            slug,
            total=total,
            cur=0,
            phase="cards",
            message="已可进入预览，正在生成要点卡…",
            preview_url=_preview_url(slug),
            preview_ready=True,
            cards_done=0,
            cards_total=cards_total,
            running=True,
            done=False,
        )

        from .job_resilience import ResilienceReport, run_with_retries, try_unit

        resilience = ResilienceReport()
        ok_cards = 0
        cards_done_teams: list[str] = []
        card_conc = _card_concurrency()
        force_cards = _force_card_rebuild()
        card_profile: list[dict[str, Any]] = []
        _set(
            slug,
            card_concurrency=card_conc,
            card_count=cards_total,
            card_profile=[],
        )

        # 1) 串行准备：读库 / fingerprint / 是否复用（与旧逻辑一致）
        prepared: list[dict[str, Any]] = []
        for i, team in enumerate(teams):
            if not _is_current(slug, token):
                return
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
                    fp = prog.items_fingerprint(items)
                    existing = prog.load_existing_card(con, issue_id, team)
                    reuse = (
                        (not force_cards)
                        and existing is not None
                        and prog.card_fingerprint(existing) == fp
                        and fp
                    )
                finally:
                    con.close()
            prepared.append(
                {
                    "i": i,
                    "team": team,
                    "items": items,
                    "fp": fp,
                    "existing": existing,
                    "reuse": bool(reuse),
                }
            )

        def _llm_build_card(_team: str, _items: list) -> dict:
            try:
                with ask_concurrency.llm_slot(pool="job"):
                    return llm.build_team_card(_team, _items, period_label)
            except ask_concurrency.AskBusyError as e:
                raise RuntimeError(f"要点卡排队超时：{e}") from e
            except ask_concurrency.JobBusyError as e:
                raise RuntimeError(f"要点卡排队超时：{e}") from e

        def _build_one(job: dict[str, Any]) -> dict[str, Any]:
            """并行 worker：try_unit + 本地 resilience；不写库。"""
            team = job["team"]
            local = ResilienceReport()
            t0 = time.perf_counter()
            start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            card = try_unit(
                lambda: _llm_build_card(team, job["items"]),
                unit=_team_label(team),
                kind="card",
                report=local,
            )
            end_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            status = "ok" if card is not None else "skipped"
            return {
                "team": team,
                "card": card,
                "local": local,
                "profile": {
                    "team": team,
                    "card_count": cards_total,
                    "card_concurrency": card_conc,
                    "card_start": start_iso,
                    "card_end": end_iso,
                    "card_latency": latency_ms,
                    "card_status": status,
                },
            }

        # 2) 受控并行 LLM（concurrency=1 即串行）；复用卡不进池
        build_jobs = [j for j in prepared if not j["reuse"]]
        built: dict[str, dict[str, Any]] = {}
        if build_jobs:
            workers = min(card_conc, len(build_jobs))
            _set(
                slug,
                phase="cards",
                message=f"要点卡生成中 · concurrency={workers}/{len(build_jobs)}",
                preview_ready=True,
                preview_url=_preview_url(slug),
                cards_done=0,
                cards_total=cards_total,
                card_concurrency=card_conc,
                card_count=cards_total,
            )
            if workers <= 1:
                for job in build_jobs:
                    if not _is_current(slug, token):
                        return
                    built[job["team"]] = _build_one(job)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {pool.submit(_build_one, job): job["team"] for job in build_jobs}
                    for fut in as_completed(futs):
                        if not _is_current(slug, token):
                            return
                        team = futs[fut]
                        built[team] = fut.result()
                        _set(
                            slug,
                            message=f"要点卡返回 · {team}",
                            card_concurrency=card_conc,
                        )

        # 3) 按 teams 原序落库 / 记账 / progress（输出顺序与串行一致）
        for job in prepared:
            if not _is_current(slug, token):
                return
            i = job["i"]
            team = job["team"]
            _set(
                slug,
                phase="cards",
                cur=i + 1,
                total=total,
                message=f"要点卡 {i + 1}/{len(teams)} · {team}",
                preview_ready=True,
                preview_url=_preview_url(slug),
                cards_done=ok_cards,
                cards_total=cards_total,
                card_concurrency=card_conc,
                card_count=cards_total,
                resilience=resilience.to_dict(),
            )
            if job["reuse"]:
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                card_profile.append(
                    {
                        "team": team,
                        "card_count": cards_total,
                        "card_concurrency": card_conc,
                        "card_start": now_iso,
                        "card_end": now_iso,
                        "card_latency": 0.0,
                        "card_status": "reuse",
                    }
                )
                ok_cards += 1
                cards_done_teams.append(team)
                _set(
                    slug,
                    message=f"要点卡复用 · {team}",
                    cards_done=ok_cards,
                    card_profile=list(card_profile),
                    resilience=resilience.to_dict(),
                )
                continue

            result = built.get(team) or _build_one(job)
            for rec in result["local"].attempts:
                resilience.record(rec)
            card_profile.append(result["profile"])
            card = result["card"]
            if card is None:
                _set(
                    slug,
                    message=f"要点卡跳过 · {team}（已重试仍失败）",
                    card_profile=list(card_profile),
                    resilience=resilience.to_dict(),
                )
                continue
            card = prog.attach_card_fingerprint(card, job["fp"])
            if not _is_current(slug, token):
                return
            with db.write_lock():
                con = db.connect()
                try:
                    _upsert_team_card(con, issue_id, team, card)
                    con.commit()
                finally:
                    con.close()
            ok_cards += 1
            cards_done_teams.append(team)
            _set(
                slug,
                cards_done=ok_cards,
                card_profile=list(card_profile),
                resilience=resilience.to_dict(),
            )

        _set(slug, card_profile=list(card_profile), card_concurrency=card_conc, card_count=cards_total)

        # 卡片全部落库后统一刷一次骨架 lead（减少 write_lock）
        if cards_done_teams:
            with db.write_lock():
                con = db.connect()
                try:
                    row = con.execute(
                        "SELECT draft_json FROM issues WHERE id=?", (issue_id,)
                    ).fetchone()
                    try:
                        data = json.loads(row["draft_json"] or "{}") if row else {}
                    except (json.JSONDecodeError, TypeError):
                        data = {}
                    if not isinstance(data, dict):
                        data = {}
                    data = prog.mark_phase(
                        data, prog.PHASE_CARDS, cards_done=cards_done_teams
                    )
                    data["lead"] = (
                        f"要点卡进度 {ok_cards}/{cards_total}"
                        + ("：" + "、".join(cards_done_teams[-3:]) if cards_done_teams else "")
                        + "。关系与周报壳仍在生成，完成后自动刷新。"
                    )
                    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    _write_partial_draft(con, issue_id, data, stamp)
                    con.commit()
                finally:
                    con.close()

        if not _is_current(slug, token):
            return
        if teams and ok_cards == 0:
            _set(
                slug,
                running=False,
                done=False,
                error="全部要点卡生成失败："
                + resilience.summary_message(ok_units=0, unit_label="卡"),
                error_code="preview_cards_failed",
                final_status="failed",
                resilience=resilience.to_dict(),
                phase="cards",
                preview_ready=True,
                preview_url=_preview_url(slug),
            )
            return

        st_now = job_store.get(KIND, slug, _defaults(slug))
        force_run = bool(st_now.get("_force"))

        with db.write_lock():
            con = db.connect()
            try:
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
                try:
                    data_mid = json.loads(r["draft_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    data_mid = {}
                if not isinstance(data_mid, dict) or not data_mid:
                    data_mid = prog.build_skeleton_draft(
                        slug=slug,
                        period_label=period_label or "",
                        version=issue_row.get("version"),
                        teams=teams,
                    )
                # candidate 级指纹：未变且非 force → 复用周报壳+关系（须在 mark_draft_stale 之前）
                rel_fp = prog.relation_input_fingerprint(
                    candidates=bundle.get("relation_candidates") or [],
                    team_cards=bundle.get("team_cards"),
                    item_rows=bundle.get("item_rows") or [],
                )
                if (
                    not force_run
                    and prog.relation_input_fingerprint_matches(data_mid, rel_fp)
                    and data_mid.get("_preview_gate_ok")
                    and isinstance(data_mid.get("relations"), list)
                    and data_mid.get("relations")
                    and not data_mid.get("_preview_building")
                    and not data_mid.get("_preview_gate_stale")
                    and not data_mid.get("_stale")
                ):
                    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    data = prog.attach_relation_input_fingerprint(data_mid, rel_fp)
                    data = prog.finalize_preview_flags(data, stamp=stamp)
                    _write_partial_draft(con, issue_id, data, stamp)
                    con.commit()
                    _set(
                        slug,
                        running=False,
                        done=True,
                        error=None,
                        phase="done",
                        message="候选与要点卡未变，复用上次关系与周报壳",
                        preview_url=f"/{slug}?preview=1&edit=1",
                        preview_ready=True,
                        cards_done=ok_cards,
                        cards_total=cards_total,
                        final_status="ok",
                        resilience=resilience.to_dict(),
                    )
                    return

                db.mark_draft_stale(con, issue_id)
                data_mid = prog.mark_phase(
                    data_mid, prog.PHASE_DRAFT, cards_done=cards_done_teams
                )
                data_mid.pop("_stale", None)
                stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                _write_partial_draft(con, issue_id, data_mid, stamp)
                con.commit()
            finally:
                con.close()

        _set(
            slug,
            phase="draft",
            cur=cards_total + 1,
            total=total,
            message="要点卡已齐，正在生成周报草稿与关系…",
            preview_ready=True,
            preview_url=_preview_url(slug),
            cards_done=ok_cards,
            cards_total=cards_total,
            resilience=resilience.to_dict(),
        )
        if not _is_current(slug, token):
            return

        def _build_draft():
            with ask_concurrency.llm_slot(pool="job"):
                return llm.build_issue_draft(
                    issue_row,
                    bundle["team_cards"],
                    bundle["relation_candidates"],
                    bundle["first_names"],
                    bundle["external_items"],
                    prev_summary,
                )

        try:
            data = run_with_retries(
                _build_draft,
                unit="issue_draft",
                kind="draft",
                report=resilience,
            )
        except Exception as e:
            _set(
                slug,
                running=False,
                done=False,
                error=f"草稿生成失败（已自动重试）：{str(e).replace(chr(10), ' ')[:180]}",
                error_code="preview_draft_failed",
                final_status="failed",
                resilience=resilience.to_dict(),
                phase="draft",
                preview_ready=True,
                preview_url=_preview_url(slug),
            )
            return

        _set(
            slug,
            phase="relations",
            message="正在整理关系卡…",
            preview_ready=True,
            preview_url=_preview_url(slug),
            resilience=resilience.to_dict(),
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
        data = prog.mark_phase(data, prog.PHASE_RELATIONS, cards_done=cards_done_teams)

        from . import relation_gate as _rg
        from .relation_display import _sync_relation_kpi, reader_visible

        data, dropped_rels = _rg.filter_ungrounded_relations(data, bundle["item_rows"])
        n_reader = sum(
            1 for r in (data.get("relations") or []) if isinstance(r, dict) and reader_visible(r)
        )
        _sync_relation_kpi(data, n_reader)

        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        if not _is_current(slug, token):
            return

        with db.write_lock():
            con = db.connect()
            try:
                payload = json.dumps(data, ensure_ascii=False)
                con.execute(
                    "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
                    (payload, stamp, issue_id),
                )
                con.commit()
                from .main import publish_blockers as _pub_blockers
                blockers = _pub_blockers(con, issue_id, payload)
            finally:
                con.close()

        if blockers:
            with db.write_lock():
                con = db.connect()
                try:
                    data_fail = prog.mark_phase(
                        data, prog.PHASE_RELATIONS, cards_done=cards_done_teams
                    )
                    data_fail["_preview_gate_error"] = blockers[:10]
                    _write_partial_draft(con, issue_id, data_fail, stamp)
                    con.commit()
                finally:
                    con.close()
            _set(
                slug,
                running=False,
                done=False,
                error="进预览前检查未通过：" + "；".join(blockers[:10]),
                error_code="preview_gate_blocked",
                phase="gate",
                message="草稿未通过论证检查（预览页仍可查看已生成内容）",
                final_status="failed",
                resilience=resilience.to_dict(),
                preview_ready=True,
                preview_url=_preview_url(slug),
            )
            return

        data = prog.finalize_preview_flags(data, stamp=stamp)
        data = prog.attach_relation_input_fingerprint(data, rel_fp)
        if resilience.skipped or dropped_rels:
            data["_preview_degraded"] = True
        if dropped_rels:
            data["_relations_dropped_ungrounded"] = dropped_rels
        else:
            data.pop("_relations_dropped_ungrounded", None)
        if not _is_current(slug, token):
            return
        with db.write_lock():
            con = db.connect()
            try:
                payload = json.dumps(data, ensure_ascii=False)
                # 只写草稿；published_json / Ask 索引仅由「确认上线」更新
                con.execute(
                    "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
                    (payload, stamp, issue_id),
                )
                db.register_entities(con, data, slug)
                note = "渐进预览完成：要点卡与草稿已通过进预览检查"
                if dropped_rels:
                    note += f"；已隐藏 {len(dropped_rels)} 张论证不足的关系卡"
                if resilience.skipped:
                    note += f"；跳过要点卡 {len(resilience.skipped)}"
                if resilience.retry_total:
                    note += f"；自动重试 {resilience.retry_total} 次"
                con.execute(
                    "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
                    (issue_id, username, "prepare_preview", "", note),
                )
                con.commit()
            finally:
                con.close()

        final = "degraded" if (resilience.skipped or dropped_rels) else "ok"
        msg = "完成，已通过检查"
        bits = []
        if dropped_rels:
            bits.append(f"已隐藏 {len(dropped_rels)} 张论证不足的关系卡")
        if resilience.skipped:
            bits.append(f"跳过 {len(resilience.skipped)} 张要点卡")
        if resilience.retry_total:
            bits.append(f"自动重试 {resilience.retry_total} 次")
        if bits:
            msg = "完成（" + "；".join(bits) + "）"
        _set(
            slug,
            running=False,
            done=True,
            error=None,
            phase="done",
            message=msg,
            preview_url=f"/{slug}?preview=1&edit=1",
            preview_ready=True,
            dropped_relations=dropped_rels[:20],
            final_status=final,
            resilience=resilience.to_dict(),
            cards_done=ok_cards,
            cards_total=cards_total,
            card_profile=list(card_profile),
            card_concurrency=card_conc,
            card_count=cards_total,
            cur=total,
            total=total,
        )
    except Exception as e:
        traceback.print_exc()
        if not _is_current(slug, token):
            return
        msg = str(e).replace("\n", " ")[:200]
        _set(
            slug,
            running=False,
            done=False,
            error=msg or "生成失败",
            final_status="failed",
        )


def _team_label(team: str) -> str:
    return (team or "团队")[:40]
