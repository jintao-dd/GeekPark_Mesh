"""Notion CRM 四库全量/增量同步到底库（问答检索用；周报仍走聚合窗口）。

环境变量（只放 .env，勿提交）：
  NOTION_TOKEN              Integration Secret
  NOTION_DATABASE_URL       四个库 URL/ID，逗号或换行分隔
  或 NOTION_DB_COMPANIES / PEOPLE / INTERACTIONS / TAKES
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import db

_log = logging.getLogger("mesh.notion_crm")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# 默认四库（Lilyann's Space）；仍可用 env 覆盖
_DEFAULT_DB_URLS = {
    "companies": "https://app.notion.com/p/Database-Company-3c44df98f91c80c59a77eb49d8971c77",
    "people": "https://app.notion.com/p/Database-Human-3c44df98f91c8076aae9ee08d579adf0",
    "interactions": "https://app.notion.com/p/Database-Interaction-3c44df98f91c8010bd86d38145b7ea66",
    "takes": "https://app.notion.com/p/Database-Take-3c44df98f91c8066b677c4970b845c1c",
}

_KIND_ORDER = ("companies", "people", "interactions", "takes")


def ensure_crm_schema(con) -> None:
    """SQLite / Postgres 均可反复调用。"""
    pg = getattr(con, "dialect", "sqlite") == "postgresql"
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    ddls = [
        f"""CREATE TABLE IF NOT EXISTS crm_sync_state(
          kind TEXT PRIMARY KEY,
          database_id TEXT,
          database_title TEXT,
          cursor_last_edited TEXT,
          last_full_at TEXT,
          last_incr_at TEXT,
          row_count INTEGER DEFAULT 0,
          status TEXT DEFAULT '',
          error TEXT,
          updated_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS crm_companies(
          id {pk},
          notion_id TEXT UNIQUE NOT NULL,
          name TEXT,
          aliases TEXT,
          one_liner TEXT,
          sector TEXT,
          stage TEXT,
          website TEXT,
          people_ids_json TEXT,
          props_json TEXT,
          last_edited_time TEXT,
          synced_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS crm_people(
          id {pk},
          notion_id TEXT UNIQUE NOT NULL,
          display_name TEXT,
          aliases TEXT,
          headline TEXT,
          company_ids_json TEXT,
          company_names TEXT,
          sector TEXT,
          location TEXT,
          email TEXT,
          wechat TEXT,
          linkedin TEXT,
          interaction_ids_json TEXT,
          take_ids_json TEXT,
          interaction_count TEXT,
          last_touched TEXT,
          props_json TEXT,
          last_edited_time TEXT,
          synced_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS crm_interactions(
          id {pk},
          notion_id TEXT UNIQUE NOT NULL,
          title TEXT,
          date_start TEXT,
          interact_type TEXT,
          people_ids_json TEXT,
          people_names TEXT,
          our_side TEXT,
          output_link TEXT,
          processed INTEGER DEFAULT 0,
          props_json TEXT,
          last_edited_time TEXT,
          synced_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS crm_takes(
          id {pk},
          notion_id TEXT UNIQUE NOT NULL,
          name TEXT,
          person_ids_json TEXT,
          person_names TEXT,
          verdict TEXT,
          scenario TEXT,
          owner TEXT,
          last_reviewed TEXT,
          is_prospect TEXT,
          props_json TEXT,
          last_edited_time TEXT,
          synced_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS crm_page_blocks(
          id {pk},
          notion_id TEXT NOT NULL,
          owner_kind TEXT DEFAULT 'takes',
          block_id TEXT UNIQUE NOT NULL,
          parent_block_id TEXT,
          ord INTEGER,
          block_type TEXT,
          text TEXT,
          page_last_edited TEXT,
          synced_at TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_crm_blocks_page ON crm_page_blocks(notion_id, ord)",
        "CREATE INDEX IF NOT EXISTS idx_crm_blocks_kind ON crm_page_blocks(owner_kind)",
        "CREATE INDEX IF NOT EXISTS idx_crm_people_name ON crm_people(display_name)",
        "CREATE INDEX IF NOT EXISTS idx_crm_people_touched ON crm_people(last_touched)",
        "CREATE INDEX IF NOT EXISTS idx_crm_companies_name ON crm_companies(name)",
        "CREATE INDEX IF NOT EXISTS idx_crm_ix_date ON crm_interactions(date_start)",
        "CREATE INDEX IF NOT EXISTS idx_crm_takes_reviewed ON crm_takes(last_reviewed)",
    ]
    for ddl in ddls:
        try:
            con.execute(ddl)
        except Exception as e:
            _log.debug("crm schema ddl skip: %s", e)


def parse_notion_id(input_s: str | None) -> str | None:
    if not input_s:
        return None
    raw = str(input_s).strip()
    m = re.match(
        r"^([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})$",
        raw,
        re.I,
    )
    if m:
        return _normalize_id(m.group(1))
    m = re.search(r"([0-9a-f]{32})", raw, re.I)
    if m:
        return _normalize_id(m.group(1))
    return None


def _normalize_id(id_s: str) -> str:
    hex32 = id_s.replace("-", "").lower()
    if len(hex32) != 32:
        return id_s
    return f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:]}"


def _token() -> str:
    return (os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY") or "").strip()


def resolve_database_map() -> dict[str, str]:
    """kind -> database_id。"""
    out: dict[str, str] = {}
    for kind, url in _DEFAULT_DB_URLS.items():
        env_key = f"NOTION_DB_{kind.upper()}"
        raw = (os.environ.get(env_key) or "").strip() or url
        nid = parse_notion_id(raw)
        if nid:
            out[kind] = nid

    multi = (os.environ.get("NOTION_DATABASE_URL") or "").strip()
    if multi:
        for part in re.split(r"[\n,]+", multi):
            part = part.strip()
            if not part:
                continue
            nid = parse_notion_id(part)
            if not nid:
                continue
            kind = _infer_kind_from_url(part)
            if kind:
                out[kind] = nid
            elif nid not in out.values():
                # 无法从 URL 推断时按尚未占用的顺序填
                for k in _KIND_ORDER:
                    if k not in out:
                        out[k] = nid
                        break
    return out


def _infer_kind_from_url(url: str) -> str | None:
    u = url.lower()
    if "company" in u or "companies" in u:
        return "companies"
    if "human" in u or "people" in u or "person" in u:
        return "people"
    if "interaction" in u:
        return "interactions"
    if "take" in u:
        return "takes"
    return None


def notion_request(method: str, path: str, body: dict | None = None, *, attempt: int = 1) -> dict:
    token = _token()
    if not token:
        raise RuntimeError("未配置 NOTION_TOKEN，请在 .env 中填写 Integration Secret")
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{NOTION_API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    timeout = float(os.environ.get("NOTION_REQUEST_TIMEOUT_MS", "30000") or "30000") / 1000.0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        if e.code == 429 and attempt < 8:
            wait = max(2 * attempt, int(e.headers.get("Retry-After") or 0))
            time.sleep(wait)
            return notion_request(method, path, body, attempt=attempt + 1)
        raise RuntimeError(f"Notion API 错误 ({e.code}): {err_body[:400]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        if attempt < 6:
            time.sleep(2 * attempt)
            return notion_request(method, path, body, attempt=attempt + 1)
        raise RuntimeError(f"Notion API 网络错误: {e}") from e


def list_block_children(block_id: str) -> list[dict]:
    results: list[dict] = []
    cursor = None
    while True:
        q = f"?start_cursor={urllib.parse.quote(cursor)}" if cursor else ""
        data = notion_request("GET", f"/blocks/{block_id}/children{q}")
        results.extend(data.get("results") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return results


def resolve_database_id(page_or_db_id: str) -> tuple[str, str]:
    """页面包装链接 → 真实 database_id；（title_hint）。与聚合 scrape-notion-database 一致。"""
    nid = parse_notion_id(page_or_db_id) or page_or_db_id
    try:
        database = notion_request("GET", f"/databases/{nid}")
        return nid, _rich_text(database.get("title")) or ""
    except RuntimeError as e:
        if "is a page, not a database" not in str(e):
            raise

    page = notion_request("GET", f"/pages/{nid}")
    page_title = ""
    props = page.get("properties") or {}
    for prop in props.values():
        if prop and prop.get("type") == "title":
            page_title = _rich_text(prop.get("title"))
            break

    children = list_block_children(nid)
    db_block = next((b for b in children if b.get("type") == "child_database"), None)
    if db_block:
        title = ((db_block.get("child_database") or {}).get("title")) or page_title
        return db_block["id"], title

    search = notion_request(
        "POST",
        "/search",
        {"filter": {"property": "object", "value": "database"}, "page_size": 100},
    )
    target = _normalize_id(nid)
    for db_obj in search.get("results") or []:
        if _normalize_id(db_obj.get("id") or "") == target:
            return db_obj["id"], _rich_text(db_obj.get("title")) or page_title
        parent = db_obj.get("parent") or {}
        if parent.get("type") == "page_id" and _normalize_id(parent.get("page_id") or "") == target:
            return db_obj["id"], _rich_text(db_obj.get("title")) or page_title

    raise RuntimeError(
        f"ID {nid} 是页面而不是数据库，且未找到子数据库。"
        "请打开数据库表格视图后复制浏览器地址栏链接。"
    )


def _rich_text(items: list | None) -> str:
    if not items:
        return ""
    return "".join((it or {}).get("plain_text") or "" for it in items).strip()


def _prop_plain(prop: dict | None) -> str:
    if not prop or not prop.get("type"):
        return ""
    t = prop["type"]
    v = prop.get(t)
    if t == "title":
        return _rich_text(prop.get("title"))
    if t == "rich_text":
        return _rich_text(prop.get("rich_text"))
    if t == "number":
        return "" if v is None else str(v)
    if t in ("select", "status"):
        return (v or {}).get("name") or ""
    if t == "multi_select":
        return ", ".join(x.get("name") or "" for x in (v or []) if x)
    if t == "date":
        if not v:
            return ""
        s = v.get("start") or ""
        if v.get("end"):
            s = f"{s} ~ {v['end']}"
        return s
    if t == "checkbox":
        return "1" if v else "0"
    if t in ("url", "email", "phone_number"):
        return v or ""
    if t == "people":
        return ", ".join(p.get("name") or p.get("id") or "" for p in (v or []))
    if t == "files":
        parts = []
        for f in v or []:
            parts.append(f.get("name") or (f.get("external") or {}).get("url") or (f.get("file") or {}).get("url") or "")
        return ", ".join(p for p in parts if p)
    if t == "relation":
        return ""  # ids via _relation_ids
    if t == "formula":
        if not v:
            return ""
        ft = v.get("type")
        if ft == "string":
            return v.get("string") or ""
        if ft == "number":
            return "" if v.get("number") is None else str(v.get("number"))
        if ft == "boolean":
            return "是" if v.get("boolean") else "否"
        if ft == "date":
            return ((v.get("date") or {}).get("start")) or ""
        return ""
    if t == "rollup":
        return _rollup_text(v)
    if t in ("created_time", "last_edited_time"):
        return v or ""
    return ""


def _rollup_text(rollup: dict | None) -> str:
    if not rollup:
        return ""
    rt = rollup.get("type")
    if rt == "number":
        return "" if rollup.get("number") is None else str(rollup.get("number"))
    if rt == "date":
        return ((rollup.get("date") or {}).get("start")) or ""
    if rt == "array":
        parts = []
        for item in rollup.get("array") or []:
            it = item.get("type")
            if it == "title":
                parts.append(_rich_text(item.get("title")))
            elif it == "rich_text":
                parts.append(_rich_text(item.get("rich_text")))
            elif it == "select":
                parts.append((item.get("select") or {}).get("name") or "")
        return ", ".join(p for p in parts if p)
    return ""


def _relation_ids(prop: dict | None) -> list[str]:
    if not prop or prop.get("type") != "relation":
        return []
    return [x.get("id") for x in (prop.get("relation") or []) if x.get("id")]


def _norm_key(name: str) -> str:
    return re.sub(r"[\s_/·\-]+", "", (name or "").lower())


def _find_prop(props: dict, *candidates: str) -> dict | None:
    if not props:
        return None
    by_norm = {_norm_key(k): v for k, v in props.items()}
    for c in candidates:
        hit = by_norm.get(_norm_key(c))
        if hit is not None:
            return hit
    # 模糊：候选是子串
    for c in candidates:
        cn = _norm_key(c)
        for k, v in by_norm.items():
            if cn and (cn in k or k in cn):
                return v
    return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@dataclass
class SyncStats:
    kind: str
    upserted: int = 0
    mode: str = "full"
    error: str = ""


def query_database(database_id: str, *, since: str | None = None) -> list[dict]:
    results: list[dict] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        if since:
            body["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {"after": since},
            }
        data = notion_request("POST", f"/databases/{database_id}/query", body)
        results.extend(data.get("results") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return results


# 有正文/子块的 block 类型 → 取 rich_text 的字段名
_TEXT_KEYS = (
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "to_do", "quote", "callout", "code", "toggle",
    "template", "child_page",
)
_MAX_BLOCK_DEPTH = 4
_MAX_PAGE_BLOCKS = 400


def _block_text(block: dict) -> str:
    """把一个 block 压成单行文本。table_row 取 cells，其余取 rich_text。"""
    t = (block or {}).get("type") or ""
    payload = block.get(t) or {}
    if t == "table_row":
        cells = payload.get("cells") or []
        return " | ".join(_rich_text(c) for c in cells).strip()
    if t in _TEXT_KEYS:
        return _rich_text(payload.get("rich_text") or payload.get("title"))
    if t in ("image", "video", "file", "pdf"):
        cap = _rich_text(payload.get("caption"))
        url = payload.get("external", {}).get("url") or payload.get("file", {}).get("url") or ""
        return (cap or url).strip()
    if t == "bookmark":
        return str(payload.get("url") or "").strip()
    return ""


def fetch_page_blocks(page_id: str, *, max_depth: int = _MAX_BLOCK_DEPTH) -> list[dict]:
    """递归拉取页面正文 → 扁平 block 列表（保序，带 depth/ord）。

    只读 API，现有 Notion 集成权限即可（comments 端点才是 403）。
    """
    out: list[dict] = []

    def walk(parent_id: str, depth: int) -> None:
        if depth > max_depth or len(out) >= _MAX_PAGE_BLOCKS:
            return
        try:
            children = list_block_children(parent_id)
        except Exception as e:
            _log.warning("crm blocks fetch %s depth=%s failed: %s", parent_id, depth, e)
            return
        for b in children:
            if len(out) >= _MAX_PAGE_BLOCKS:
                return
            bid = b.get("id") or ""
            if not bid:
                continue
            out.append(
                {
                    "block_id": bid,
                    "parent_block_id": "" if depth == 0 else parent_id,
                    "ord": len(out),
                    "block_type": b.get("type") or "",
                    "text": _block_text(b),
                    "depth": depth,
                    "has_children": bool(b.get("has_children")),
                }
            )
            if b.get("has_children"):
                walk(bid, depth + 1)

    walk(page_id, 0)
    return out


def _block_to_line(row: dict, indent: int = 0) -> str:
    """block 行 → 供 LLM 读的文本行（表格行渲染成 markdown 表）。"""
    t = row.get("block_type") or ""
    txt = (row.get("text") or "").strip()
    if t == "table_row":
        return ("  " * indent) + "| " + txt + " |"
    if t in ("heading_1", "heading_2", "heading_3"):
        lvl = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}[t]
        return ("  " * indent) + f"{lvl} {txt}".strip()
    if t == "divider":
        return ("  " * indent) + "---"
    if not txt:
        return ""
    return ("  " * indent) + txt


def page_timeline_text(con, notion_id: str, *, max_chars: int = 6000) -> str:
    """取某页正文，拼成时间线文本。无记录返回空串。"""
    try:
        rows = con.execute(
            "SELECT block_type, text FROM crm_page_blocks WHERE notion_id=? ORDER BY ord",
            (notion_id,),
        ).fetchall()
    except Exception as e:
        _log.debug("page_timeline_text failed: %s", e)
        return ""
    lines: list[str] = []
    in_table = False
    for r in rows:
        t = r["block_type"] or ""
        txt = (r["text"] or "").strip()
        if t == "table" or t == "table_row":
            if not in_table:
                lines.append("")
                in_table = True
        elif in_table:
            lines.append("")
            in_table = False
        line = _block_to_line({"block_type": t, "text": txt})
        if line:
            lines.append(line)
    text = "\n".join(lines).strip()
    return text[:max_chars]


def sync_page_blocks(
    con,
    kind: str = "takes",
    *,
    full: bool = False,
    page_limit: int = 0,
) -> dict[str, int]:
    """抓 Take（或 People）页面正文进 crm_page_blocks。

    依赖已同步的 crm_takes.notion_id / last_edited_time；增量只抓
    page_last_edited 有变的页，全量重抓全部。不抓评论（Notion 集成无权限）。
    """
    ensure_crm_schema(con)
    if kind not in ("takes", "people", "companies"):
        raise ValueError(f"unsupported kind: {kind}")
    table = _CONVERTERS[kind][0]
    rows = con.execute(
        f"SELECT notion_id, last_edited_time FROM {table} ORDER BY last_edited_time DESC"
    ).fetchall()
    if page_limit > 0:
        rows = rows[:page_limit]

    seen = 0
    fetched = 0
    for r in rows:
        nid = r["notion_id"]
        if not nid:
            continue
        seen += 1
        page_edited = r["last_edited_time"] or ""
        if not full:
            have = con.execute(
                "SELECT page_last_edited FROM crm_page_blocks WHERE notion_id=? LIMIT 1",
                (nid,),
            ).fetchone()
            if have and (have["page_last_edited"] or "") == page_edited:
                continue
        try:
            blocks = fetch_page_blocks(nid)
        except Exception as e:
            _log.warning("crm page blocks %s failed: %s", nid, e)
            continue
        fetched += 1
        con.execute("DELETE FROM crm_page_blocks WHERE notion_id=?", (nid,))
        for b in blocks:
            con.execute(
                "INSERT INTO crm_page_blocks"
                "(notion_id, owner_kind, block_id, parent_block_id, ord, block_type, text,"
                " page_last_edited, synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    nid,
                    kind,
                    b["block_id"],
                    b["parent_block_id"],
                    b["ord"],
                    b["block_type"],
                    b["text"],
                    page_edited,
                    _now(),
                ),
            )
        db.commit_retry(con)
    total = con.execute("SELECT COUNT(*) c FROM crm_page_blocks").fetchone()["c"]
    return {"pages_seen": seen, "pages_fetched": fetched, "blocks_total": int(total or 0)}


def _props_snapshot(props: dict) -> dict:
    out = {}
    for name, prop in (props or {}).items():
        out[name] = {
            "type": (prop or {}).get("type"),
            "text": _prop_plain(prop),
            "relation_ids": _relation_ids(prop),
        }
    return out


def _upsert(con, table: str, notion_id: str, cols: dict[str, Any]) -> None:
    cols = {**cols, "notion_id": notion_id, "synced_at": _now()}
    keys = list(cols.keys())
    placeholders = ",".join("?" for _ in keys)
    col_sql = ",".join(keys)
    updates = ",".join(f"{k}=excluded.{k}" for k in keys if k != "notion_id")
    con.execute(
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT(notion_id) DO UPDATE SET {updates}",
        tuple(cols[k] for k in keys),
    )


def _page_to_company(page: dict) -> dict:
    props = page.get("properties") or {}
    return {
        "name": _prop_plain(_find_prop(props, "Name", "公司名", "Company")),
        "aliases": _prop_plain(_find_prop(props, "Aliases", "别名")),
        "one_liner": _prop_plain(_find_prop(props, "One-liner", "One liner", "一句话介绍")),
        "sector": _prop_plain(_find_prop(props, "Sector", "赛道")),
        "stage": _prop_plain(_find_prop(props, "Stage", "阶段")),
        "website": _prop_plain(_find_prop(props, "Website", "官网")),
        "people_ids_json": json.dumps(_relation_ids(_find_prop(props, "People", "关联人物")), ensure_ascii=False),
        "props_json": json.dumps(_props_snapshot(props), ensure_ascii=False),
        "last_edited_time": page.get("last_edited_time") or "",
    }


def _page_to_person(page: dict) -> dict:
    props = page.get("properties") or {}
    email_p = _find_prop(props, "Email", "邮箱")
    wechat_p = _find_prop(props, "WeChat", "微信", "Wechat")
    return {
        "display_name": _prop_plain(_find_prop(props, "Display name", "Name", "姓名")),
        "aliases": _prop_plain(_find_prop(props, "Aliases", "别名")),
        "headline": _prop_plain(_find_prop(props, "Headline / Role", "Headline", "Role", "头衔", "职位")),
        "company_ids_json": json.dumps(_relation_ids(_find_prop(props, "Company", "所属公司")), ensure_ascii=False),
        "company_names": "",
        "sector": _prop_plain(_find_prop(props, "Sector", "赛道")),
        "location": _prop_plain(_find_prop(props, "Location", "地点")),
        "email": _prop_plain(email_p),
        "wechat": _prop_plain(wechat_p),
        "linkedin": _prop_plain(_find_prop(props, "LinkedIn URL", "LinkedIn", "linkedin slug", "LinkedIn slug")),
        "interaction_ids_json": json.dumps(_relation_ids(_find_prop(props, "Interactions", "沟通记录")), ensure_ascii=False),
        "take_ids_json": json.dumps(_relation_ids(_find_prop(props, "Take", "Takes", "观点")), ensure_ascii=False),
        "interaction_count": _prop_plain(_find_prop(props, "Interaction count", "沟通次数")),
        "last_touched": _prop_plain(_find_prop(props, "Last touched", "最近接触")),
        "props_json": json.dumps(_props_snapshot(props), ensure_ascii=False),
        "last_edited_time": page.get("last_edited_time") or "",
    }


def _page_to_interaction(page: dict) -> dict:
    props = page.get("properties") or {}
    processed = _prop_plain(_find_prop(props, "Processed", "是否已处理"))
    return {
        "title": _prop_plain(_find_prop(props, "Title", "沟通标题", "Name")),
        "date_start": (_prop_plain(_find_prop(props, "Date", "日期")) or "").split(" ~ ")[0],
        "interact_type": _prop_plain(_find_prop(props, "Type", "类型")),
        "people_ids_json": json.dumps(_relation_ids(_find_prop(props, "People", "沟通对象")), ensure_ascii=False),
        "people_names": "",
        "our_side": _prop_plain(_find_prop(props, "Our side", "我方参与人")),
        "output_link": _prop_plain(_find_prop(props, "Output link", "产出链接")),
        "processed": 1 if processed in ("1", "是", "true", "True", "YES", "yes") else 0,
        "props_json": json.dumps(_props_snapshot(props), ensure_ascii=False),
        "last_edited_time": page.get("last_edited_time") or "",
    }


def _page_to_take(page: dict) -> dict:
    props = page.get("properties") or {}
    return {
        "name": _prop_plain(_find_prop(props, "Name", "摘录标题", "Title")),
        "person_ids_json": json.dumps(_relation_ids(_find_prop(props, "Person", "相关人物", "People")), ensure_ascii=False),
        "person_names": "",
        "verdict": _prop_plain(_find_prop(props, "Verdict", "判断", "结论")),
        "scenario": _prop_plain(_find_prop(props, "场景 Scenario", "Scenario", "场景")),
        "owner": _prop_plain(_find_prop(props, "Owner", "负责人")),
        "last_reviewed": (_prop_plain(_find_prop(props, "Last reviewed", "最近审阅")) or "").split(" ~ ")[0],
        "is_prospect": _prop_plain(_find_prop(props, "Is prospect", "是否潜在对象")),
        "props_json": json.dumps(_props_snapshot(props), ensure_ascii=False),
        "last_edited_time": page.get("last_edited_time") or "",
    }


_CONVERTERS = {
    "companies": ("crm_companies", _page_to_company),
    "people": ("crm_people", _page_to_person),
    "interactions": ("crm_interactions", _page_to_interaction),
    "takes": ("crm_takes", _page_to_take),
}


def _set_sync_state(con, kind: str, **fields: Any) -> None:
    row = con.execute("SELECT kind FROM crm_sync_state WHERE kind=?", (kind,)).fetchone()
    fields = {**fields, "updated_at": _now()}
    if not row:
        cols = {"kind": kind, **fields}
        keys = list(cols.keys())
        con.execute(
            f"INSERT INTO crm_sync_state ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            tuple(cols[k] for k in keys),
        )
    else:
        sets = ", ".join(f"{k}=?" for k in fields)
        con.execute(f"UPDATE crm_sync_state SET {sets} WHERE kind=?", (*fields.values(), kind))


def sync_kind(con, kind: str, database_id: str, *, full: bool = False) -> SyncStats:
    ensure_crm_schema(con)
    table, converter = _CONVERTERS[kind]
    stats = SyncStats(kind=kind)
    state = con.execute("SELECT * FROM crm_sync_state WHERE kind=?", (kind,)).fetchone()
    since = None
    if not full and state and state["cursor_last_edited"]:
        since = state["cursor_last_edited"]
        stats.mode = "incremental"
    else:
        stats.mode = "full"

    try:
        database_id, title_hint = resolve_database_id(database_id)
        meta = notion_request("GET", f"/databases/{database_id}")
        title = _rich_text(meta.get("title")) or title_hint or kind
        pages = query_database(database_id, since=since)
        max_edited = since or ""
        for page in pages:
            nid = page.get("id")
            if not nid:
                continue
            cols = converter(page)
            _upsert(con, table, nid, cols)
            stats.upserted += 1
            le = page.get("last_edited_time") or ""
            if le > max_edited:
                max_edited = le
        count_row = con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()
        row_count = int(count_row["c"] if count_row else 0)
        # cursor 用「刚同步时的时间」避免漏掉同秒写入；全量后也记下 max
        cursor = max_edited or _iso_now()
        # Notion filter 是 after（开区间），游标回退 1 秒不必要；用 max_edited 即可
        patch = {
            "database_id": database_id,
            "database_title": title,
            "cursor_last_edited": cursor,
            "row_count": row_count,
            "status": "ok",
            "error": "",
        }
        if stats.mode == "full":
            patch["last_full_at"] = _now()
        else:
            patch["last_incr_at"] = _now()
        _set_sync_state(con, kind, **patch)
        db.commit_retry(con)
    except Exception as e:
        stats.error = str(e)
        _set_sync_state(con, kind, status="error", error=str(e)[:500])
        try:
            db.commit_retry(con)
        except Exception:
            pass
        raise
    return stats


def resolve_relation_names(con) -> dict[str, int]:
    """用本地四库互查填 company_names / people_names / person_names。"""
    ensure_crm_schema(con)
    companies = {
        r["notion_id"]: r["name"] or ""
        for r in con.execute("SELECT notion_id, name FROM crm_companies")
    }
    people = {
        r["notion_id"]: r["display_name"] or ""
        for r in con.execute("SELECT notion_id, display_name FROM crm_people")
    }
    updated = {"people": 0, "interactions": 0, "takes": 0}

    for r in con.execute("SELECT notion_id, company_ids_json FROM crm_people"):
        ids = json.loads(r["company_ids_json"] or "[]")
        names = ", ".join(companies.get(i, "") for i in ids if companies.get(i))
        con.execute("UPDATE crm_people SET company_names=? WHERE notion_id=?", (names, r["notion_id"]))
        updated["people"] += 1

    for r in con.execute("SELECT notion_id, people_ids_json FROM crm_interactions"):
        ids = json.loads(r["people_ids_json"] or "[]")
        names = ", ".join(people.get(i, "") for i in ids if people.get(i))
        con.execute(
            "UPDATE crm_interactions SET people_names=? WHERE notion_id=?",
            (names, r["notion_id"]),
        )
        updated["interactions"] += 1

    for r in con.execute("SELECT notion_id, person_ids_json FROM crm_takes"):
        ids = json.loads(r["person_ids_json"] or "[]")
        names = ", ".join(people.get(i, "") for i in ids if people.get(i))
        con.execute("UPDATE crm_takes SET person_names=? WHERE notion_id=?", (names, r["notion_id"]))
        updated["takes"] += 1

    db.commit_retry(con)
    return updated


def sync_all(
    *,
    full: bool = False,
    con=None,
    with_blocks: bool = True,
    blocks_full: bool = False,
) -> dict:
    """同步四库。full=True 忽略游标全量 upsert。

    with_blocks=True 时同时抓 Take 页面正文（详细沟通记录）进 crm_page_blocks；
    正文增量靠页面 last_edited_time 比对，blocks_full=True 强制重抓。
    """
    own = con is None
    if own:
        con = db.connect()
    ensure_crm_schema(con)
    db_map = resolve_database_map()
    missing = [k for k in _KIND_ORDER if k not in db_map]
    if missing:
        raise RuntimeError(f"缺少数据库配置: {missing}；请设 NOTION_DATABASE_URL 或 NOTION_DB_*")
    if not _token():
        raise RuntimeError("未配置 NOTION_TOKEN")

    results: list[dict] = []
    for kind in _KIND_ORDER:
        st = sync_kind(con, kind, db_map[kind], full=full)
        results.append(
            {
                "kind": st.kind,
                "mode": st.mode,
                "upserted": st.upserted,
                "error": st.error,
            }
        )
        _log.info("notion crm sync %s mode=%s upserted=%s", st.kind, st.mode, st.upserted)

    rel = resolve_relation_names(con)
    counts = {
        "companies": con.execute("SELECT COUNT(*) c FROM crm_companies").fetchone()["c"],
        "people": con.execute("SELECT COUNT(*) c FROM crm_people").fetchone()["c"],
        "interactions": con.execute("SELECT COUNT(*) c FROM crm_interactions").fetchone()["c"],
        "takes": con.execute("SELECT COUNT(*) c FROM crm_takes").fetchone()["c"],
    }
    # 页面正文（Take 详细沟通记录）默认一起抓；失败只记日志，不拖垮四库同步。
    blocks: dict[str, int] = {}
    if with_blocks:
        try:
            blocks = sync_page_blocks(con, "takes", full=full or blocks_full)
        except Exception as e:
            _log.warning("crm page blocks sync failed: %s", e)
            blocks = {"error": str(e)[:300]}
    if own:
        con.close()
    return {
        "ok": True,
        "full": full,
        "results": results,
        "resolved": rel,
        "counts": counts,
        "blocks": blocks,
    }


def sync_status(con=None) -> dict:
    own = con is None
    if own:
        con = db.connect()
    ensure_crm_schema(con)
    rows = [dict(r) for r in con.execute("SELECT * FROM crm_sync_state ORDER BY kind")]
    counts = {}
    for kind, table in (
        ("companies", "crm_companies"),
        ("people", "crm_people"),
        ("interactions", "crm_interactions"),
        ("takes", "crm_takes"),
    ):
        try:
            counts[kind] = con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        except Exception:
            counts[kind] = 0
    try:
        counts["page_blocks"] = con.execute(
            "SELECT COUNT(*) c FROM crm_page_blocks"
        ).fetchone()["c"]
    except Exception:
        counts["page_blocks"] = 0
    if own:
        con.close()
    return {"states": rows, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # 尝试加载本地 .env（不覆盖已有环境变量）
    for env_path in (
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "内容聚合", ".env"),
    ):
        p = os.path.abspath(env_path)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass

    ap = argparse.ArgumentParser(description="Sync Notion CRM into Mesh")
    ap.add_argument("cmd", nargs="?", default="sync", choices=["sync", "status"])
    ap.add_argument("--full", action="store_true", help="忽略游标，全量 upsert")
    args = ap.parse_args(argv)

    db.init_db(seed=False)
    if args.cmd == "status":
        print(json.dumps(sync_status(), ensure_ascii=False, indent=2, default=str))
        return 0
    out = sync_all(full=args.full)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
