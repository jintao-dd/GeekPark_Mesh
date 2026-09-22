"""Notion CRM 增量 → T3「硅谷 BD 团队创业者数据库」来源，生成预览前自动接入。

约定（2026-09-22 确认）：
1. 增量窗口 = **上次 CRM 同步以来有变动的行**（比较 crm_sync_state.cursor_last_edited）。
2. source_label 固定「硅谷 BD 团队创业者数据库」（见 prompts/extract_T3.md 白名单）。

数据流：
    notion_crm.sync_all(full=False)   # 拉最新增量进 crm_* 四表
      → build_incremental_digest     # 把「上次同步以来变动」的行拼成 T3 可抽取文本
      → llm.extract_source(T3, 硅谷 BD 团队)
      → 写入 sources(channel='crm') + items（owner=硅谷 BD 团队）

接入点：preview_job._run 在跑挖掘/预览前调用 maybe_ingest_for_issue；
失败只记日志，不阻断预览。
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from . import db

_log = logging.getLogger("mesh.crm_ingest")

SOURCE_LABEL = "硅谷 BD 团队创业者数据库"
SOURCE_TITLE = "Notion CRM 增量 · 创业者数据库"
SOURCE_FILENAME = "notion-crm-incremental.txt"
SOURCE_TEAM = "硅谷 BD 团队"
SOURCE_STYPE = "T3"
CHANNEL = "crm"

# 无游标（从未同步）时的兜底窗口：只看最近 N 天有更新的行，避免全量灌入
_FALLBACK_DAYS = 14
# 每类最多写入摘要的行数，防止一次增量过大撑爆抽取提示词
_CAP_PER_KIND = 80


def _enabled() -> bool:
    return (os.environ.get("MESH_CRM_PREVIEW_INGEST") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _day(iso: str) -> str:
    return (iso or "")[:10]


def _fallback_since() -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=_FALLBACK_DAYS)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _pre_cursors(con) -> dict[str, str]:
    """同步前捕获各库游标 = 上次同步已覆盖到的 last_edited_time。"""
    out: dict[str, str] = {}
    try:
        rows = con.execute("SELECT kind, cursor_last_edited FROM crm_sync_state").fetchall()
    except Exception:
        return out
    for r in rows:
        out[r["kind"]] = (r["cursor_last_edited"] or "").strip()
    return out


def _select_changed(con, kind: str, since: str) -> list[dict]:
    """取出 last_edited_time > since 的行（严格大于，与 Notion after 语义一致）。"""
    table = {
        "companies": "crm_companies",
        "people": "crm_people",
        "interactions": "crm_interactions",
        "takes": "crm_takes",
    }[kind]
    cols = {
        "companies": "name, sector, stage, one_liner, last_edited_time",
        "people": "display_name, company_names, headline, location, last_touched, last_edited_time",
        "interactions": "title, date_start, interact_type, people_names, our_side, last_edited_time",
        "takes": "name, person_names, verdict, scenario, owner, last_reviewed, is_prospect, last_edited_time",
    }[kind]
    try:
        rows = con.execute(
            f"SELECT {cols} FROM {table} "
            f"WHERE last_edited_time IS NOT NULL AND TRIM(last_edited_time) <> '' "
            f"AND last_edited_time > ? ORDER BY last_edited_time DESC",
            (since,),
        ).fetchall()
    except Exception as e:  # 表不存在 / 字段缺失不应阻断
        _log.warning("crm_ingest select %s failed: %s", kind, e)
        return []
    return [dict(r) for r in rows]


def _fmt_lines(kind: str, rows: list[dict], cap: int) -> list[str]:
    out: list[str] = []
    for r in rows[:cap]:
        if kind == "people":
            parts = [
                f"姓名：{r.get('display_name') or ''}",
                f"公司：{r.get('company_names') or ''}",
                f"职位：{r.get('headline') or ''}",
                f"城市：{r.get('location') or ''}",
                f"最近接触：{_day(r.get('last_touched') or '')}",
            ]
        elif kind == "companies":
            parts = [
                f"公司：{r.get('name') or ''}",
                f"赛道：{r.get('sector') or ''}",
                f"阶段：{r.get('stage') or ''}",
                f"一句话：{r.get('one_liner') or ''}",
            ]
        elif kind == "interactions":
            parts = [
                f"日期：{_day(r.get('date_start') or '')}",
                f"形式：{r.get('interact_type') or ''}",
                f"对象：{r.get('people_names') or ''}",
                f"我方：{r.get('our_side') or ''}",
                f"标题：{r.get('title') or ''}",
            ]
        else:  # takes
            parts = [
                f"对象：{r.get('person_names') or r.get('name') or ''}",
                f"判断：{r.get('verdict') or ''}",
                f"场景：{r.get('scenario') or ''}",
                f"负责人：{r.get('owner') or ''}",
                f"最近审阅：{_day(r.get('last_reviewed') or '')}",
                f"潜在：{r.get('is_prospect') or ''}",
            ]
        out.append("- " + "｜".join(p for p in parts if p.split("：", 1)[-1].strip()))
    return out


_KIND_TITLE = {
    "people": "human（人）",
    "companies": "company（公司）",
    "interactions": "interaction（接触）",
    "takes": "take（判断）",
}


def build_incremental_digest(con, *, since_map: dict[str, str] | None = None) -> dict:
    """把「上次同步以来变动」的行拼成 T3 抽取用文本。无变动则 text 为空。"""
    since_map = since_map or {}
    fallback = _fallback_since()
    counts: dict[str, int] = {}
    truncated: dict[str, int] = {}
    blocks: list[str] = []
    since_used: dict[str, str] = {}

    for kind in ("people", "companies", "interactions", "takes"):
        since = (since_map.get(kind) or "").strip() or fallback
        since_used[kind] = since
        rows = _select_changed(con, kind, since)
        counts[kind] = len(rows)
        if len(rows) > _CAP_PER_KIND:
            truncated[kind] = len(rows) - _CAP_PER_KIND
        lines = _fmt_lines(kind, rows, _CAP_PER_KIND)
        if not lines:
            continue
        blocks.append(f"## {_KIND_TITLE[kind]}（{len(rows)} 行）\n" + "\n".join(lines))

    total = sum(counts.values())
    if not total:
        return {"text": "", "counts": counts, "truncated": truncated, "since": since_used, "until": _now_iso()}

    since_min = min(since_used.values()) if since_used else ""
    header = [
        "【Notion CRM 增量 · 创业者数据库】",
        "来源：Notion CRM（human / company / interaction / take 四类表）",
        f"增量窗口：{since_min} ~ {_now_iso()}（上次 CRM 同步以来有更新的行）",
        "说明：本文件是「硅谷 BD 团队创业者数据库」的增量导出，按 T3 抽取规则处理。",
    ]
    if truncated:
        header.append("截断：" + "、".join(f"{k} 省略 {v} 行" for k, v in truncated.items()))
    text = "\n".join(header) + "\n\n" + "\n\n".join(blocks) + "\n"
    return {
        "text": text,
        "counts": counts,
        "truncated": truncated,
        "since": since_used,
        "until": _now_iso(),
    }


def sync_then_build(*, con=None) -> dict:
    """先增量同步四库，再按「同步前的游标」构建增量摘要。"""
    from . import notion_crm

    own = con is None
    if own:
        con = db.connect()
    try:
        if not notion_crm._token():
            return {"ok": False, "reason": "no_token", "counts": {}, "text": ""}
        notion_crm.ensure_crm_schema(con)
        pre = _pre_cursors(con)
        sync = notion_crm.sync_all(full=False, con=con)
        digest = build_incremental_digest(con, since_map=pre)
        digest["ok"] = True
        digest["sync"] = sync.get("results")
        return digest
    finally:
        if own:
            con.close()


def _extract_items(issue, *, source_id: int, text: str) -> list[dict]:
    """按 T3 抽取（LLM 调用，不碰 DB）。"""
    from . import llm

    items, _split_meta = llm.extract_source(
        SOURCE_STYPE,
        SOURCE_TEAM,
        SOURCE_TITLE,
        text or "",
        period_start=issue["date_start"] or "",
        period_end=issue["date_end"] or "",
        period_label=issue["period_label"] or "",
        channel=CHANNEL,
        skip_split=False,
        source_id=source_id,
    )
    return items


def _write_items(con, issue, *, source_id: int, items: list[dict]) -> int:
    """写入 items（复用 main.source_extract 的落库口径）。"""
    from .attribution import apply_attribution_to_item

    con.execute("DELETE FROM items WHERE source_id=?", (source_id,))
    slug = issue["slug"]
    for it in items:
        item_stype = it.get("item_stype") or SOURCE_STYPE
        attr = apply_attribution_to_item(
            it, source_team=SOURCE_TEAM, segment_team=it.get("_segment_team"),
        )
        owner = attr.owner_team
        blocked = int(it.get("blocked") or 0)
        con.execute(
            """INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,
               source_label,pointer,blocked,owner_team,channel,source_labels,owner_provenance,llm_owner_team_hint)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                issue["id"], source_id, owner or it.get("team"), item_stype, it["zone"], it["level"],
                it["kind"], it["text"], json.dumps(it["entities"], ensure_ascii=False),
                json.dumps(it["roles"], ensure_ascii=False), json.dumps(it["signals"], ensure_ascii=False),
                it["source_label"], it["pointer"], blocked, owner, CHANNEL,
                json.dumps([it["source_label"]] if it.get("source_label") else [], ensure_ascii=False),
                attr.provenance, attr.llm_owner_team_hint or it.get("llm_owner_team_hint"),
            ),
        )
        for name in it["entities"]:
            if name:
                con.execute(
                    "INSERT INTO entities(name,kind,first_issue) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING",
                    (name, "auto", slug),
                )
    return len(items)


def _existing_source(con, issue_id: int) -> dict | None:
    row = con.execute(
        "SELECT id, meta FROM sources WHERE issue_id=? AND channel=? ORDER BY id LIMIT 1",
        (issue_id, CHANNEL),
    ).fetchone()
    return dict(row) if row else None


def _prev_since(meta_json: str) -> dict[str, str]:
    try:
        meta = json.loads(meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    since = (meta or {}).get("since") or {}
    return {k: str(v) for k, v in since.items() if v} if isinstance(since, dict) else {}


def ingest_for_issue(con, issue, *, digest: dict, items: list[dict] | None = None) -> dict:
    """把增量摘要作为一条 sources(channel='crm') 接入该期，并抽取成 items。

    复用同一期已有的 CRM 来源行（重复生成预览不叠加）。
    items 由调用方在写锁外抽好传入；为 None 时在本函数内同步抽取（调用方需自行持锁）。
    """
    text = (digest or {}).get("text") or ""
    if not text.strip():
        return {"ingested": False, "reason": "no_changes", "items": 0}

    meta = json.dumps(
        {
            "crm_ingest": 1,
            "source_label": SOURCE_LABEL,
            "since": digest.get("since") or {},
            "until": digest.get("until") or "",
            "counts": digest.get("counts") or {},
            "truncated": digest.get("truncated") or {},
        },
        ensure_ascii=False,
    )

    ex = _existing_source(con, issue["id"])
    if ex:
        sid = ex["id"]
        con.execute(
            "UPDATE sources SET stype=?, team=?, title=?, filename=?, text=?, meta=?, extracted=0 WHERE id=?",
            (SOURCE_STYPE, SOURCE_TEAM, SOURCE_TITLE, SOURCE_FILENAME, text, meta, sid),
        )
    else:
        from .db_conn import insert_id

        sid = insert_id(
            con,
            "INSERT INTO sources(issue_id,stype,team,title,filename,raw_path,text,meta,channel) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (issue["id"], SOURCE_STYPE, SOURCE_TEAM, SOURCE_TITLE, SOURCE_FILENAME, "", text, meta, CHANNEL),
        )

    if items is None:
        items = _extract_items(issue, source_id=sid, text=text)
    n_items = _write_items(con, issue, source_id=sid, items=items)
    con.execute("UPDATE sources SET extracted=1 WHERE id=?", (sid,))
    return {"ingested": True, "source_id": sid, "items": n_items, "counts": digest.get("counts") or {}}


def maybe_ingest_for_issue(slug: str) -> dict:
    """生成预览前的入口：同步 + 构建 + 抽取接入。失败只记日志，不阻断预览。

    网络（Notion 同步、LLM 抽取）在写锁之外；仅 DB 写入持写锁。
    同一期重复生成预览时，增量窗口取「上次接入时的 since ∪ 本次」，
    保证重跑不丢上一批增量行（只增不减）。
    """
    if not _enabled():
        return {"ingested": False, "reason": "disabled"}

    con = db.connect()
    try:
        issue = con.execute(
            "SELECT id, slug, date_start, date_end, period_label, status FROM issues WHERE slug=?",
            (slug,),
        ).fetchone()
        if not issue:
            return {"ingested": False, "reason": "no_issue"}
        issue = dict(issue)
        prev = _prev_since((_existing_source(con, issue["id"]) or {}).get("meta") or "")
        digest = sync_then_build(con=con)
        if not digest.get("ok"):
            return {"ingested": False, "reason": digest.get("reason") or "sync_failed"}
        # 合并窗口：同一期重跑时，取更早的 since，避免覆盖掉上一批增量
        merged = dict(digest.get("since") or {})
        for kind, iso in prev.items():
            cur = merged.get(kind)
            merged[kind] = iso if not cur else min(cur, iso)
        if merged != (digest.get("since") or {}):
            digest = build_incremental_digest(con, since_map=merged)
            digest["ok"] = True
        if not (digest.get("text") or "").strip():
            return {"ingested": False, "reason": "no_changes", "counts": digest.get("counts") or {}}
    except Exception as e:
        _log.warning("crm_ingest sync failed slug=%s: %s", slug, e)
        return {"ingested": False, "reason": f"error: {e}"}
    finally:
        con.close()

    # LLM 抽取不持锁
    try:
        items = _extract_items(issue, source_id=-1, text=digest["text"])
    except Exception as e:
        _log.warning("crm_ingest extract failed slug=%s: %s", slug, e)
        return {"ingested": False, "reason": f"extract_error: {e}"}

    try:
        with db.write_lock():
            con = db.connect()
            try:
                out = ingest_for_issue(con, issue, digest=digest, items=items)
                db.commit_retry(con)
            finally:
                con.close()
        _log.info("crm_ingest ok slug=%s items=%s counts=%s", slug, out.get("items"), out.get("counts"))
        return out
    except Exception as e:
        _log.warning("crm_ingest write failed slug=%s: %s", slug, e)
        return {"ingested": False, "reason": f"write_error: {e}"}
