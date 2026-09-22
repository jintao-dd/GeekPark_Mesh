"""统一 chunk 索引：发刊后从 search_fts + item_facts 物化，供向量与融合检索。

切分策略（A1，small-to-big）：
  - 长正文按标题层级/段落 + 滑窗切成「小子块」（layer=child），带重叠，检索精度用；
  - 同时保留一个「父块」（layer=section|item）装完整正文，命中子块后展开父块供生成；
  - 短正文不切，直接自成一块（既当检索块也当生成块）。
  - embedding 只喂小子块 + 父块标题，天然短，不再 [:4000] 粗暴砍尾。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import embeddings, tokenize as tok
from . import db_conn


# ---- 切分参数（可被 env 覆盖，便于 tmesh 调参）----
def _int_env(name: str, default: int) -> int:
    import os

    try:
        return max(1, int(os.environ.get(name, "") or default))
    except (ValueError, TypeError):
        return default


def _child_target() -> int:
    """子块目标字符数（小切片检索）。"""
    return _int_env("MESH_CHUNK_CHILD_CHARS", 320)


def _child_overlap() -> int:
    """相邻子块重叠字符数（保上下文连续）。"""
    return _int_env("MESH_CHUNK_CHILD_OVERLAP", 64)


def _embed_char_cap() -> int:
    """embed 单条上限：宽松保护，不再当默认截断手段。"""
    return _int_env("MESH_EMBED_CHAR_CAP", 8000)


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6}\s+|[一二三四五六七八九十]+[、.．]|\d+[、.．)]\s*)")
_PARA_SPLIT_RE = re.compile(r"\n{2,}")


def _split_paragraphs(body: str) -> list[str]:
    """按标题层级/空行把正文切成语义段，短段合并到目标窗口。"""
    body = (body or "").strip()
    if not body:
        return []
    # 先按空行分段
    raw_paras = [p.strip() for p in _PARA_SPLIT_RE.split(body) if p.strip()]
    if not raw_paras:
        raw_paras = [body]
    # 再按行内标题切：标题行独立起段
    segs: list[str] = []
    for para in raw_paras:
        cur: list[str] = []
        for line in para.splitlines():
            if _HEADING_RE.match(line) and cur:
                segs.append("\n".join(cur).strip())
                cur = [line]
            else:
                cur.append(line)
        if cur:
            segs.append("\n".join(cur).strip())
    return [s for s in segs if s]


def _window_children(body: str, *, target: int, overlap: int) -> list[str]:
    """把正文切成带重叠的小子块；先按段，再对超长段滑窗。"""
    body = (body or "").strip()
    if not body:
        return []
    if len(body) <= target:
        return [body]
    segs = _split_paragraphs(body)
    children: list[str] = []
    buf = ""
    for seg in segs:
        # 段本身超长：先冲刷 buf，再对该段滑窗
        if len(seg) > target:
            if buf:
                children.append(buf.strip())
                buf = ""
            start = 0
            step = max(1, target - overlap)
            while start < len(seg):
                children.append(seg[start : start + target].strip())
                start += step
            continue
        if not buf:
            buf = seg
        elif len(buf) + 1 + len(seg) <= target:
            buf = f"{buf}\n{seg}"
        else:
            children.append(buf.strip())
            # 重叠：把上一块尾部带入下一块开头
            tail = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = f"{tail}\n{seg}".strip() if tail else seg
    if buf.strip():
        children.append(buf.strip())
    return [c for c in children if c]


# chunk 物化方案版本：切分策略变更后 +1，启动时若 DB 记录落后则强制 rebuild_all
# （父块 chunk_id 不变，靠数量判断无法察觉「新增子块」，必须显式版本触发）
CHUNK_SCHEME_VERSION = "2-small-to-big"
_SCHEME_KEY = "chunk_scheme_version"


def _cid(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _insert_chunk(con, rec: dict) -> str:
    cid = rec["chunk_id"]
    dialect = getattr(con, "dialect", "sqlite")
    con.execute(
        db_conn.chunk_index_upsert_sql(dialect),
        (
            cid, rec["issue_slug"], rec.get("issue_id"), rec.get("date_end") or "",
            rec["layer"], rec.get("section") or "", rec.get("stype") or "",
            rec.get("owner_team") or "", rec.get("entity_name") or "",
            rec.get("title") or "", rec["body"], rec.get("source_label") or "",
            rec.get("item_id"), rec.get("source_id"), rec.get("toks") or "",
            json.dumps(rec.get("meta") or {}, ensure_ascii=False),
        ),
    )
    return cid


def _insert_parent_and_children(con, parent: dict) -> int:
    """small-to-big：插入一个父块（完整正文，生成用）+ 若干小子块（检索用）。

    - 父块 layer 为传入的 parent_layer（section|item），meta.is_parent=True；
    - 子块 layer=child，meta.parent_id 指向父块，命中后可展开父块正文；
    - 短正文只产生父块自身（父块也可被检索），不再冗余切一个同内容子块。
    返回写入的 chunk 数（含父）。
    """
    body = (parent.get("body") or "").strip()
    title = (parent.get("title") or "").strip()
    if not body and not title:
        return 0
    parent_layer = parent.get("layer") or "section"
    parent_id = parent["chunk_id"]
    section = parent.get("section") or ""
    owner_team = parent.get("owner_team") or ""

    # 父块：完整正文，供生成展开
    pmeta = dict(parent.get("meta") or {})
    pmeta["is_parent"] = True
    _insert_chunk(con, {
        **parent,
        "layer": parent_layer,
        "body": body or title,
        "toks": tok.tokenize_for_index(f"{title} {body} {owner_team}"),
        "meta": pmeta,
    })
    n = 1

    # 子块：仅当正文足够长才切；短正文父块已可被检索，无需冗余
    children = _window_children(
        body, target=_child_target(), overlap=_child_overlap()
    ) if len(body) > _child_target() else []
    for ord_i, ctext in enumerate(children):
        ctext = ctext.strip()
        if not ctext:
            continue
        cid = _cid("child", parent_id, str(ord_i), ctext[:60])
        _insert_chunk(con, {
            "chunk_id": cid,
            "issue_slug": parent["issue_slug"],
            "issue_id": parent.get("issue_id"),
            "date_end": parent.get("date_end") or "",
            "layer": "child",
            "section": section,
            "stype": parent.get("stype") or "",
            "owner_team": owner_team,
            "entity_name": parent.get("entity_name") or "",
            "title": title,
            "body": ctext,
            "source_label": parent.get("source_label") or "",
            "item_id": parent.get("item_id"),
            "source_id": parent.get("source_id"),
            "toks": tok.tokenize_for_index(f"{title} {ctext}"),
            "meta": {
                "parent_id": parent_id,
                "parent_layer": parent_layer,
                "child_ord": ord_i,
                "stype": parent.get("stype") or "",
            },
        })
        n += 1
    return n


def rebuild_issue(con, issue_id: int, *, items: bool = True) -> int:
    row = con.execute(
        "SELECT id, slug, status, date_end FROM issues WHERE id=?", (issue_id,),
    ).fetchone()
    if not row:
        return 0
    slug = row["slug"]
    con.execute("DELETE FROM chunk_index WHERE issue_slug=?", (slug,))
    con.execute(
        "DELETE FROM chunk_embeddings WHERE chunk_id NOT IN (SELECT chunk_id FROM chunk_index)"
    )
    if row["status"] != "published":
        return 0
    n_written = _materialize_issue_chunks(con, row, items=items)
    # 清掉长父块的旧向量（small-to-big 下长父块不再 embed，靠子块覆盖；
    # 残留旧向量会被 vector_search JOIN 出来，正是截断问题的复发点）
    con.execute(
        f"""DELETE FROM chunk_embeddings WHERE chunk_id IN (
              SELECT chunk_id FROM chunk_index
              WHERE issue_slug=? AND layer IN ('section','item') AND LENGTH(body) > ?
            )""",
        (slug, _child_target()),
    )
    return n_written


def _materialize_issue_chunks(con, row, *, items: bool) -> int:
    slug = row["slug"]
    n = 0
    for r in con.execute(
        "SELECT section, title, body, date_end FROM search_fts WHERE issue_slug=?", (slug,),
    ):
        body = (r["body"] or "").strip()
        if not body:
            continue
        title = (r["title"] or "").strip()
        n += _insert_parent_and_children(con, {
            "chunk_id": _cid("fts", slug, r["section"], title, body[:80]),
            "issue_slug": slug,
            "issue_id": row["id"],
            "date_end": r["date_end"] or row["date_end"] or "",
            "layer": "section",
            "section": r["section"],
            "title": title,
            "body": body,
        })
    if items:
        for r in con.execute(
            """SELECT issue_id, item_id, source_id, owner_team, stype, primary_name,
                      text_snippet, source_label, date_end
               FROM item_facts WHERE issue_slug=?""",
            (slug,),
        ):
            body = (r["text_snippet"] or "").strip()
            title = (r["primary_name"] or "").strip()
            if not body and not title:
                continue
            n += _insert_parent_and_children(con, {
                "chunk_id": _cid("item", slug, str(r["item_id"]), title),
                "issue_slug": slug,
                "issue_id": r["issue_id"],
                "date_end": r["date_end"] or "",
                "layer": "item",
                "section": "抽取条目",
                "stype": r["stype"],
                "owner_team": r["owner_team"],
                "title": title,
                "body": body or title,
                "source_label": r["source_label"],
                "item_id": r["item_id"],
                "source_id": r["source_id"],
                "meta": {"stype": r["stype"]},
            })
    return n


def embedding_counts_for_slug(con, issue_slug: str, model: str | None = None) -> tuple[int, int]:
    """返回 (应 embed 的 chunk 总数, 已有 embedding 数)。

    small-to-big：长父块不 embed（由子块覆盖），总数需一致排除，否则永远到不了 100%。
    """
    model = model or embeddings.model_name()
    total = int(
        con.execute(
            f"SELECT COUNT(*) c FROM chunk_index c WHERE issue_slug=? {_EMBED_SKIP_LONG_PARENT}",
            (issue_slug, _child_target()),
        ).fetchone()["c"]
    )
    done = int(
        con.execute(
            """SELECT COUNT(*) c FROM chunk_index c
               INNER JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = ?
               WHERE c.issue_slug=?""",
            (model, issue_slug),
        ).fetchone()["c"]
    )
    return total, done


def embed_all_missing(con, batch: int | None = None, max_rounds: int = 200) -> int:
    bs = batch or embeddings.batch_size()
    total = 0
    for _ in range(max_rounds):
        n = embed_missing_chunks(con, batch=bs)
        if n <= 0:
            break
        total += n
    return total


# small-to-big：只 embed 小子块 + 未被切分的短父块（长父块已由子块覆盖，
# embed 长父块既浪费又会触发截断，正是我们要消除的问题）。
_EMBED_SKIP_LONG_PARENT = "AND NOT (c.layer IN ('section','item') AND LENGTH(c.body) > ?)"


def embed_missing_chunks(con, batch: int | None = None) -> int:
    """为尚无 embedding 的 chunk 批量写入向量。"""
    if not embeddings.is_configured():
        return 0
    bs = batch or embeddings.batch_size()
    model = embeddings.model_name()
    rows = con.execute(
        f"""SELECT c.chunk_id, c.title, c.body FROM chunk_index c
           LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = ?
           WHERE e.chunk_id IS NULL {_EMBED_SKIP_LONG_PARENT}
           ORDER BY c.chunk_id LIMIT ?""",
        (model, _child_target(), bs),
    ).fetchall()
    if not rows:
        return 0
    cap = _embed_char_cap()
    texts = [f"{r['title'] or ''}\n{r['body'] or ''}"[:cap] for r in rows]
    vecs = embeddings.embed_texts(texts)
    if len(rows) != len(vecs):
        err = embeddings.last_error()
        if err:
            print(f"[mesh] embed batch skipped ({len(rows)} rows): {err}", flush=True)
        if bs > 1 and len(rows) > 1:
            half = max(1, len(rows) // 2)
            n = 0
            for sub_rows, sub_texts in ((rows[:half], texts[:half]), (rows[half:], texts[half:])):
                if not sub_rows:
                    continue
                sub_vecs = embeddings.embed_texts(sub_texts)
                if len(sub_rows) == len(sub_vecs):
                    n += _write_embeddings(con, model, sub_rows, sub_vecs)
            return n
        return 0
    return _write_embeddings(con, model, rows, vecs)


def embed_missing_for_slug(con, issue_slug: str, batch: int | None = None) -> int:
    """为某期尚无 embedding 的 chunk 批量写入向量。"""
    if not embeddings.is_configured():
        return 0
    bs = batch or embeddings.batch_size()
    model = embeddings.model_name()
    rows = con.execute(
        f"""SELECT c.chunk_id, c.title, c.body FROM chunk_index c
           LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = ?
           WHERE e.chunk_id IS NULL AND c.issue_slug=? {_EMBED_SKIP_LONG_PARENT}
           ORDER BY c.chunk_id LIMIT ?""",
        (model, issue_slug, _child_target(), bs),
    ).fetchall()
    if not rows:
        return 0
    cap = _embed_char_cap()
    texts = [f"{r['title'] or ''}\n{r['body'] or ''}"[:cap] for r in rows]
    vecs = embeddings.embed_texts(texts)
    if len(rows) != len(vecs):
        err = embeddings.last_error()
        if err:
            print(f"[mesh] embed batch skipped ({issue_slug}, {len(rows)} rows): {err}", flush=True)
        if bs > 1 and len(rows) > 1:
            half = max(1, len(rows) // 2)
            n = 0
            for sub_rows, sub_texts in ((rows[:half], texts[:half]), (rows[half:], texts[half:])):
                if not sub_rows:
                    continue
                sub_vecs = embeddings.embed_texts(sub_texts)
                if len(sub_rows) == len(sub_vecs):
                    n += _write_embeddings(con, model, sub_rows, sub_vecs)
            return n
        return 0
    return _write_embeddings(con, model, rows, vecs)


def embed_all_missing_for_slug(
    con,
    issue_slug: str,
    batch: int | None = None,
    max_rounds: int = 200,
) -> int:
    bs = batch or embeddings.batch_size()
    total = 0
    for _ in range(max_rounds):
        n = embed_missing_for_slug(con, issue_slug, batch=bs)
        if n <= 0:
            break
        total += n
    return total


def _write_embeddings(con, model: str, rows, vecs) -> int:
    n = 0
    for r, vec in zip(rows, vecs):
        if not vec:
            continue
        con.execute(
            db_conn.chunk_embedding_upsert_sql(getattr(con, "dialect", "sqlite")),
            (r["chunk_id"], model, len(vec), json.dumps(vec)),
        )
        n += 1
    return n


def expand_children_to_parents(con, hits: list[dict]) -> list[dict]:
    """small-to-big：命中小子块后，用父块完整正文替换 body 供生成。

    - 只影响 layer=child 的命中；保留其检索 score/rank/source（精准召回不变）；
    - 同一父块被多个子块命中时去重，保留分最高的一条；
    - 找不到父块（异常）则保留子块原样，不丢证据。
    """
    if not hits:
        return hits
    parent_ids: set[str] = set()
    for h in hits:
        meta = h.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        pid = meta.get("parent_id") or h.get("parent_id")
        if h.get("layer") == "child" and pid:
            parent_ids.add(str(pid))
    if not parent_ids:
        return hits
    placeholders = ",".join("?" * len(parent_ids))
    pmap: dict[str, dict] = {}
    try:
        for r in con.execute(
            f"""SELECT chunk_id, section, title, body, owner_team, stype, item_id,
                       source_label, date_end, issue_slug
                FROM chunk_index WHERE chunk_id IN ({placeholders})""",
            list(parent_ids),
        ):
            pmap[str(r["chunk_id"])] = dict(r)
    except Exception:
        return hits

    out: list[dict] = []
    seen_parent: dict[str, int] = {}  # parent_id -> index in out
    for h in hits:
        meta = h.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        pid = str(meta.get("parent_id") or h.get("parent_id") or "")
        if h.get("layer") == "child" and pid and pid in pmap:
            if pid in seen_parent:
                continue  # 同父块已收录分更高的一条
            p = pmap[pid]
            merged = dict(h)
            merged["body"] = p.get("body") or h.get("body") or ""
            merged["title"] = h.get("title") or p.get("title") or ""
            merged["chunk_id"] = pid
            merged["layer"] = meta.get("parent_layer") or "section"
            merged["_matched_child"] = h.get("chunk_id")
            seen_parent[pid] = len(out)
            out.append(merged)
        else:
            out.append(h)
    return out


def maybe_rebuild_for_scheme(con) -> bool:
    """切分方案升级后一次性重建全部已发布期的 chunk_index。

    返回 True 表示本次触发了重建（调用方据此把相关期 embedding 置 pending 重跑）。
    父块 chunk_id 不随方案变化，纯靠数量判断察觉不到「该切子块了」，故用显式版本。
    """
    from . import db as _db

    try:
        cur = _db.get_setting(con, _SCHEME_KEY, "")
    except Exception:
        cur = ""
    if str(cur or "") == CHUNK_SCHEME_VERSION:
        return False
    n = rebuild_all(con)
    try:
        _db.set_setting(con, _SCHEME_KEY, CHUNK_SCHEME_VERSION)
    except Exception:
        pass
    print(f"[mesh] chunk scheme upgraded → {CHUNK_SCHEME_VERSION}, rebuilt {n} chunks", flush=True)
    return True


def rebuild_all(con) -> int:
    n = 0
    for r in con.execute("SELECT id FROM issues WHERE status='published'"):
        n += rebuild_issue(con, r["id"])
    return n
