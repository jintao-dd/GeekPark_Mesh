"""Notion CRM 增量 → T3「硅谷 BD 团队创业者数据库」来源，生成预览前自动接入。

约定（2026-09-23 修订）：
1. 增量窗口 = **(本期消费锚点, now]**，锚点存 crm_cross_anchor，**与同步游标解耦**。
   旧的 `min(上期 since, 同步游标)` 有两个毛病：裸同步会推高游标导致窗口变空；
   而 min() 又把窗口永久钉在最早那天，重跑一次就白抽一遍。
   现在：每期首次接入写入锚点，窗口只在成功后推进，重跑复用同一锚点（幂等）。
2. 窗口起点默认取「本期 date_start」——审计口径是本期及其之前未被消费的改动，
   而不是「上次同步以来」。这样任何上游同步都不会把某一期的增量吃掉。
3. source_label 固定「硅谷 BD 团队创业者数据库」（见 prompts/extract_T3.md 白名单）。

数据流：
    notion_crm.sync_all(full=False)   # 拉最新增量进 crm_* 四表
      → build_digest                 # 属性增量 + Take 页面正文（详细沟通记录）
      → 按行分块 → llm.extract_source(T3, 硅谷 BD 团队) × N 块
      → 写入 sources(channel='crm') + items（owner=硅谷 BD 团队）

接入点：preview_job._run 在跑挖掘/预览前调用 maybe_ingest_for_issue；
失败只记日志，不阻断预览，但状态会回报给预览页（不再静默跳过）。
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

_SYNC_KINDS = ("people", "companies", "interactions", "takes")

# 首次接入时的兜底回看天数（锚点表缺失且本期无 date_start 时用）
_FALLBACK_DAYS = 14
# 安全上限：只在行数异常时兜底，正常不再截断
_SAFETY_MAX_ROWS = 4000
# 分块：CRM 是「大量短行」形态，不能套 aggregator 的 heading 拆段器
# （其 _MIN_SEGMENT=80 会把 40~60 字的 CRM 行整条丢掉）。
# 改为按行数/字符数切块，每块独立抽取，彻底消除「80 行之外被截断」。
_CHUNK_LINES = 40
_CHUNK_CHARS = 6000
# 单次接入的分块上限（防止异常超大窗口把 LLM 打爆）；超出的行会被计入 truncated
_MAX_CHUNKS = 60
# 页面正文（Take 时间线）单页与总量的软上限，超出时显式记录在 truncated
_BLOCK_PAGE_LIMIT = 60
_BLOCK_CHARS_PER_PAGE = 1200


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


def ensure_anchor_schema(con) -> None:
    """锚点表：记录每期「消费到哪」，与 crm_sync_state 的同步游标解耦。

    只有真正消费成功的窗口才推进，裸 sync 永不触碰，因此任何上游同步
    都不会把某一期的增量吃掉。SQLite / Postgres 均可反复调用。
    """
    pg = getattr(con, "dialect", "sqlite") == "postgresql"
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    try:
        con.execute(
            f"""CREATE TABLE IF NOT EXISTS crm_cross_anchor(
              id {pk},
              issue_id INTEGER NOT NULL,
              kind TEXT NOT NULL,
              anchor_edited TEXT,
              updated_at TEXT,
              UNIQUE(issue_id, kind)
            )"""
        )
    except Exception as e:
        _log.debug("crm anchor ddl skip: %s", e)


def _load_anchors(con, issue_id: int) -> dict[str, str]:
    try:
        rows = con.execute(
            "SELECT kind, anchor_edited FROM crm_cross_anchor WHERE issue_id=?",
            (issue_id,),
        ).fetchall()
    except Exception:
        return {}
    return {r["kind"]: (r["anchor_edited"] or "") for r in rows}


def _issue_window_floor(issue: dict) -> str:
    """窗口默认起点 = 本期 date_start。没有就用 14 天兜底。"""
    ds = (issue or {}).get("date_start") or ""
    if ds:
        # issues.date_start 是 YYYY-MM-DD；补成当天 00:00:00 的 Notion ISO 形态
        return f"{ds}T00:00:00.000Z" if len(ds) == 10 else ds
    return _fallback_since()


def resolve_window(con, issue: dict) -> dict[str, str]:
    """本期各库窗口起点。

    锚点已存在 → 沿用（重跑幂等，不回溯、不放大）。
    首次接入 → 取本期 date_start（下限），保证「本期未被消费的改动」都能进来。
    """
    anchors = _load_anchors(con, int(issue["id"]))
    floor = _issue_window_floor(issue)
    return {k: (anchors.get(k) or floor) for k in _SYNC_KINDS}


def commit_anchors(con, issue_id: int, until: str, kinds=_SYNC_KINDS) -> None:
    """消费成功后推进锚点。仅在真正写入 items 之后调用。"""
    now = _now_iso()
    for kind in kinds:
        try:
            con.execute(
                "INSERT INTO crm_cross_anchor(issue_id,kind,anchor_edited,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(issue_id,kind) DO UPDATE SET "
                "anchor_edited=excluded.anchor_edited, updated_at=excluded.updated_at",
                (int(issue_id), kind, until, now),
            )
        except Exception as e:
            _log.warning("crm anchor commit failed kind=%s: %s", kind, e)


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


def _fmt_lines(kind: str, rows: list[dict], cap: int = 0) -> list[str]:
    out: list[str] = []
    for r in (rows[:cap] if cap else rows):
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
    "timeline": "take 页面正文（详细沟通记录）",
}


def build_timeline_lines(con, since: str) -> tuple[list[str], dict]:
    """取 page_last_edited > since 的 Take 页面正文，拼成可抽取行。

    这部分就是「每个人的详细沟通记录」：逐次沟通、判断变更、跟进记录 Log 表。
    之前完全没进过周报交叉面。返回 (lines, meta)。
    """
    from .notion_crm import page_timeline_text

    meta = {"pages": 0, "included": 0, "omitted": 0}
    try:
        rows = con.execute(
            "SELECT DISTINCT notion_id, page_last_edited FROM crm_page_blocks "
            "WHERE page_last_edited IS NOT NULL AND TRIM(page_last_edited) <> '' "
            "AND page_last_edited > ? ORDER BY page_last_edited DESC",
            (since,),
        ).fetchall()
    except Exception as e:
        _log.warning("crm timeline select failed: %s", e)
        return [], meta

    meta["pages"] = len(rows)
    lines: list[str] = []
    for r in rows:
        if meta["included"] >= _BLOCK_PAGE_LIMIT:
            meta["omitted"] = len(rows) - meta["included"]
            break
        tl = page_timeline_text(con, r["notion_id"], max_chars=_BLOCK_CHARS_PER_PAGE)
        if not tl.strip():
            continue
        who = ""
        try:
            t = con.execute(
                "SELECT person_names, name FROM crm_takes WHERE notion_id=?", (r["notion_id"],)
            ).fetchone()
            if t:
                who = t["person_names"] or t["name"] or ""
        except Exception:
            pass
        lines.append(f"### {who or r['notion_id']}（页面更新 {_day(r['page_last_edited'])}）")
        lines.extend(f"  {ln}" for ln in tl.splitlines() if ln.strip())
        lines.append("")
        meta["included"] += 1
    return lines, meta


def build_digest(con, *, since_map: dict[str, str] | None = None) -> dict:
    """构建本期增量摘要：四库属性 + Take 页面正文。

    不再对行数做硬截断（分块抽取替代）；truncated 只在极端情况下兜底且必须可见。
    """
    since_map = since_map or {}
    fallback = _fallback_since()
    counts: dict[str, int] = {}
    truncated: dict[str, int] = {}
    since_used: dict[str, str] = {}
    blocks: list[str] = []

    for kind in _SYNC_KINDS:
        since = (since_map.get(kind) or "").strip() or fallback
        since_used[kind] = since
        rows = _select_changed(con, kind, since)
        counts[kind] = len(rows)
        if len(rows) > _SAFETY_MAX_ROWS:
            truncated[kind] = len(rows) - _SAFETY_MAX_ROWS
            rows = rows[:_SAFETY_MAX_ROWS]
        lines = _fmt_lines(kind, rows)
        if not lines:
            continue
        blocks.append(f"## {_KIND_TITLE[kind]}（{len(rows)} 行）\n" + "\n".join(lines))

    # Take 页面正文单独一段：同一次抽取里带到，但分块时自成块
    tl_since = (since_map.get("blocks") or "").strip() or fallback
    since_used["blocks"] = tl_since
    tl_lines, tl_meta = build_timeline_lines(con, tl_since)
    counts["blocks"] = tl_meta["pages"]
    if tl_meta["omitted"]:
        truncated["blocks"] = tl_meta["omitted"]
    if tl_lines:
        blocks.append(
            f"## {_KIND_TITLE['timeline']}（{tl_meta['included']} 页）\n" + "\n".join(tl_lines)
        )

    total = sum(v for k, v in counts.items() if k != "blocks") + tl_meta["included"]
    if not total:
        return {
            "text": "",
            "counts": counts,
            "truncated": truncated,
            "since": since_used,
            "until": _now_iso(),
            "timeline": tl_meta,
        }

    since_min = min(since_used.values()) if since_used else ""
    header = [
        "【Notion CRM 增量 · 创业者数据库】",
        "来源：Notion CRM（human / company / interaction / take 四类表 + take 页面正文）",
        f"增量窗口：{since_min} ~ {_now_iso()}（本期锚点以来有更新的行）",
        "说明：本文件是「硅谷 BD 团队创业者数据库」的增量导出，按 T3 抽取规则处理。",
    ]
    if truncated:
        header.append("截断：" + "、".join(f"{k} 省略 {v} 条" for k, v in truncated.items()))
    text = "\n".join(header) + "\n\n" + "\n\n".join(blocks) + "\n"
    return {
        "text": text,
        "counts": counts,
        "truncated": truncated,
        "since": since_used,
        "until": _now_iso(),
        "timeline": tl_meta,
    }


def split_digest_chunks(text: str, *, max_lines: int = _CHUNK_LINES,
                        max_chars: int = _CHUNK_CHARS) -> list[str]:
    """把 digest 文本按行数/字符数切块。

    CRM 数据是「大量短行」，不能套 aggregator 的 heading 拆段器
    （_MIN_SEGMENT=80 会把 40~60 字的行整条丢掉）。这里按行切，
    每个块都带原 header，保证抽取提示词有窗口上下文。
    """
    lines = (text or "").splitlines()
    if not lines:
        return []
    # header = 首个段标记("## ")或首条内容行("- ")之前的部分
    head_end = len(lines)
    for i, ln in enumerate(lines):
        if ln.startswith("## ") or ln.startswith("- "):
            head_end = i
            break
    header = lines[:head_end]
    body = lines[head_end:]
    if not body:
        return ["\n".join(header).strip()] if any(h.strip() for h in header) else []

    chunks: list[str] = []
    cur: list[str] = []
    cur_chars = 0
    head_txt = "\n".join(header)

    def flush():
        nonlocal cur, cur_chars
        if cur:
            chunks.append((head_txt + "\n\n" + "\n".join(cur)).strip())
            cur = []
            cur_chars = 0

    for ln in body:
        # "## " 开头：类型段的边界，尽量不从中间切开
        if ln.startswith("## ") and cur and cur_chars >= max_chars // 2:
            flush()
        cur.append(ln)
        cur_chars += len(ln) + 1
        if len(cur) >= max_lines or cur_chars >= max_chars:
            flush()
    flush()
    return [c for c in chunks if c.strip()]


# 兼容旧调用名
def build_incremental_digest(con, *, since_map: dict[str, str] | None = None) -> dict:
    return build_digest(con, since_map=since_map)


def sync_then_build(*, con=None, issue: dict | None = None) -> dict:
    """先增量同步四库，再按**本期锚点**构建增量摘要。

    窗口不再来自 crm_sync_state（同步游标），而是 crm_cross_anchor：
    裸同步/管理台同步都不会再吃掉任何一期的增量。
    """
    from . import notion_crm

    own = con is None
    if own:
        con = db.connect()
    try:
        if not notion_crm._token():
            return {"ok": False, "reason": "no_token", "counts": {}, "text": ""}
        notion_crm.ensure_crm_schema(con)
        ensure_anchor_schema(con)
        sync = notion_crm.sync_all(full=False, con=con)
        since_map = resolve_window(con, issue) if issue else {}
        digest = build_digest(con, since_map=since_map)
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


def extract_all_chunks(issue, *, text: str, source_id: int = -1) -> tuple[list[dict], int]:
    """按行分块并行抽取，合并结果。

    取代原来的「整份文本一次抽取 + 80 行硬截断」：
    块内是合法抽取单元，且不会触发 aggregator 的短行过滤。
    并行走 llm 既有的段级信号量（MESH_SEGMENT_EXTRACT_MAX_CONCURRENT）限流，
    单块失败/超时只跳过该块（记日志），不拖垮整次接入。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from . import llm

    chunks = split_digest_chunks(text)
    if not chunks:
        return [], 0
    if len(chunks) > _MAX_CHUNKS:
        _log.warning("crm chunks capped %s -> %s", len(chunks), _MAX_CHUNKS)
        chunks = chunks[:_MAX_CHUNKS]

    workers = max(1, min(len(chunks), int(os.environ.get("MESH_CRM_CHUNK_WORKERS") or 4)))
    sem = getattr(llm, "_SEG_EXTRACT_SEMAPHORE", None)
    budget = getattr(llm, "_SEG_EXTRACT_TIMEOUT_S", 120)

    def _one(i_ch: tuple[int, str]) -> list[dict]:
        i, ch = i_ch
        if sem is not None and not sem.acquire(timeout=budget):
            _log.warning("crm chunk %s/%s semaphore timeout", i + 1, len(chunks))
            return []
        try:
            return _extract_items(issue, source_id=source_id, text=ch)
        except Exception as e:
            _log.warning("crm chunk extract failed %s/%s: %s", i + 1, len(chunks), e)
            return []
        finally:
            if sem is not None:
                sem.release()

    items: list[dict] = []
    ok_chunks = 0
    if len(chunks) == 1 or workers == 1:
        for i, ch in enumerate(chunks):
            got = _one((i, ch))
            if got:
                ok_chunks += 1
            items.extend(got)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_one, (i, ch)): i for i, ch in enumerate(chunks)}
            for fut in as_completed(futs):
                try:
                    got = fut.result(timeout=budget)
                except Exception as e:
                    _log.warning("crm chunk future failed %s/%s: %s", futs[fut] + 1, len(chunks), e)
                    continue
                if got:
                    ok_chunks += 1
                items.extend(got)

    if ok_chunks == 0:
        raise RuntimeError(f"全部 {len(chunks)} 个分块抽取失败")
    return items, len(chunks)


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

    ensure_anchor_schema(con)
    meta = json.dumps(
        {
            "crm_ingest": 1,
            "source_label": SOURCE_LABEL,
            "since": digest.get("since") or {},
            "until": digest.get("until") or "",
            "counts": digest.get("counts") or {},
            "truncated": digest.get("truncated") or {},
            "timeline": digest.get("timeline") or {},
            "chunks": digest.get("chunks") or 0,
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
        items, n_chunks = extract_all_chunks(issue, text=text, source_id=sid)
        digest["chunks"] = n_chunks
    n_items = _write_items(con, issue, source_id=sid, items=items)
    con.execute("UPDATE sources SET extracted=1 WHERE id=?", (sid,))
    # 只消费成功才推进锚点：窗口收敛，重跑不会重抽同一段
    commit_anchors(con, issue["id"], digest.get("until") or _now_iso())
    return {
        "ingested": True,
        "source_id": sid,
        "items": n_items,
        "counts": digest.get("counts") or {},
        "truncated": digest.get("truncated") or {},
        "chunks": digest.get("chunks") or 0,
    }


def maybe_ingest_for_issue(slug: str) -> dict:
    """生成预览前的入口：按本期锚点同步 + 构建 + 分块抽取 + 接入。

    网络（Notion 同步、LLM 抽取）在写锁之外；仅 DB 写入持写锁。
    返回值带 status，供预览页显式展示（ok / truncated / no_changes / failed / disabled），
    不再把失败和「本来就没变更」混成一个静默的 ingested=False。
    """
    if not _enabled():
        return {"ingested": False, "status": "disabled", "reason": "disabled"}

    con = db.connect()
    window: dict[str, str] = {}
    try:
        issue = con.execute(
            "SELECT id, slug, date_start, date_end, period_label, status FROM issues WHERE slug=?",
            (slug,),
        ).fetchone()
        if not issue:
            return {"ingested": False, "status": "failed", "reason": "no_issue"}
        issue = dict(issue)
        ensure_anchor_schema(con)
        window = resolve_window(con, issue)
        digest = sync_then_build(con=con, issue=issue)
        if not digest.get("ok"):
            return {
                "ingested": False,
                "status": "failed",
                "reason": digest.get("reason") or "sync_failed",
                "window": window,
            }
    except Exception as e:
        _log.warning("crm_ingest sync failed slug=%s: %s", slug, e)
        return {"ingested": False, "status": "failed", "reason": f"error: {e}", "window": window}
    finally:
        con.close()

    if not (digest.get("text") or "").strip():
        return {
            "ingested": False,
            "status": "no_changes",
            "reason": "no_changes",
            "counts": digest.get("counts") or {},
            "window": window,
        }

    # 分块 LLM 抽取（不持锁）
    try:
        items, n_chunks = extract_all_chunks(issue, text=digest["text"], source_id=-1)
        digest["chunks"] = n_chunks
    except Exception as e:
        _log.warning("crm_ingest extract failed slug=%s: %s", slug, e)
        return {
            "ingested": False,
            "status": "failed",
            "reason": f"extract_error: {e}",
            "window": window,
        }

    try:
        with db.write_lock():
            con = db.connect()
            try:
                out = ingest_for_issue(con, issue, digest=digest, items=items)
                db.commit_retry(con)
            finally:
                con.close()
        _log.info(
            "crm_ingest ok slug=%s items=%s chunks=%s counts=%s",
            slug, out.get("items"), out.get("chunks"), out.get("counts"),
        )
        out["status"] = "truncated" if out.get("truncated") else "ok"
        out["window"] = window
        return out
    except Exception as e:
        _log.warning("crm_ingest write failed slug=%s: %s", slug, e)
        return {"ingested": False, "status": "failed", "reason": f"write_error: {e}", "window": window}
