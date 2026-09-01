"""统一 chunk 索引：发刊后从 search_fts + item_facts 物化，供向量与融合检索。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import embeddings, tokenize as tok
from . import db_conn


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
    n = 0
    for r in con.execute(
        "SELECT section, title, body, date_end FROM search_fts WHERE issue_slug=?", (slug,),
    ):
        body = (r["body"] or "").strip()
        if not body:
            continue
        title = (r["title"] or "").strip()
        _insert_chunk(con, {
            "chunk_id": _cid("fts", slug, r["section"], title, body[:80]),
            "issue_slug": slug,
            "issue_id": row["id"],
            "date_end": r["date_end"] or row["date_end"] or "",
            "layer": "section",
            "section": r["section"],
            "title": title,
            "body": body,
            "toks": tok.tokenize_for_index(f"{title} {body}"),
        })
        n += 1
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
            _insert_chunk(con, {
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
                "toks": tok.tokenize_for_index(f"{title} {body} {r['owner_team']}"),
                "meta": {"stype": r["stype"]},
            })
            n += 1
    embed_all_missing(con)
    return n


def embed_all_missing(con, batch: int | None = None, max_rounds: int = 200) -> int:
    bs = batch or embeddings.batch_size()
    total = 0
    for _ in range(max_rounds):
        n = embed_missing_chunks(con, batch=bs)
        if n <= 0:
            break
        total += n
    return total


def embed_missing_chunks(con, batch: int | None = None) -> int:
    """为尚无 embedding 的 chunk 批量写入向量。"""
    if not embeddings.is_configured():
        return 0
    bs = batch or embeddings.batch_size()
    model = embeddings.model_name()
    rows = con.execute(
        """SELECT c.chunk_id, c.title, c.body FROM chunk_index c
           LEFT JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = ?
           WHERE e.chunk_id IS NULL ORDER BY c.chunk_id LIMIT ?""",
        (model, bs),
    ).fetchall()
    if not rows:
        return 0
    texts = [f"{r['title'] or ''}\n{r['body'] or ''}"[:4000] for r in rows]
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


def rebuild_all(con) -> int:
    n = 0
    for r in con.execute("SELECT id FROM issues WHERE status='published'"):
        n += rebuild_issue(con, r["id"])
    return n
