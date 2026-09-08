#!/usr/bin/env python3
"""R14 Index/Data 核查（只读）：4408–4412 是否在 Retrieval 语料面。

不改 FTS/Query/Chunk/Vector/Rerank。
用法（容器内）：
  PYTHONPATH=/srv/mesh python /tmp/_check_r14_index.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

IDS = [4408, 4409, 4410, 4411, 4412]
QUERY = "视频号本周播放较好的片子"
SLUG = "2026-09-08"


def _row(con, sql, params=()):
    return con.execute(sql, params).fetchone()


def _rows(con, sql, params=()):
    return list(con.execute(sql, params).fetchall() or [])


def _snip(s: str | None, n: int = 120) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    return t[:n] + ("…" if len(t) > n else "")


def check_one(con, iid: int) -> dict:
    out: dict = {"item_id": iid}
    item = _row(
        con,
        "SELECT id, text, team, owner_team, stype, source_id, issue_id, level, kind, "
        "source_label, entities, pointer, blocked "
        "FROM items WHERE id=?",
        (iid,),
    )
    if not item:
        out.update(
            {
                "exists_in_items": False,
                "in_item_facts": False,
                "in_item_facts_fts": False,
                "in_search_fts": False,
                "in_chunk_index": False,
                "direct_fts_hit": False,
                "attribution_hint": "B_missing_items",
            }
        )
        return out

    text = item["text"] or ""
    title = text.split("：", 1)[0][:80] if text else ""
    body = text
    team = item["owner_team"] or item["team"] or ""
    stype = item["stype"] or ""
    issue_id = item["issue_id"]
    issue_slug = ""
    issue_status = ""
    if issue_id:
        iss = _row(con, "SELECT slug, status FROM issues WHERE id=?", (issue_id,))
        if iss:
            issue_slug = iss["slug"] or ""
            issue_status = iss["status"] or ""

    out.update(
        {
            "exists_in_items": True,
            "issue_id": issue_id,
            "issue_slug": issue_slug,
            "issue_status": issue_status,
            "team": team,
            "stype": stype,
            "level": item["level"],
            "kind": item["kind"],
            "blocked": item["blocked"],
            "source_label": item["source_label"],
            "pointer": item["pointer"],
            "title": _snip(title, 80),
            "body_snip": _snip(body, 160),
            "has_play_token": bool(re.search(r"播放|点赞|视频号|片子|观看", body)),
        }
    )

    # item_facts
    if_row = _row(
        con,
        "SELECT item_id, issue_slug, date_end, owner_team, stype, primary_name, "
        "substr(text_snippet,1,160) AS snip, toks "
        "FROM item_facts WHERE item_id=? LIMIT 1",
        (iid,),
    )
    out["in_item_facts"] = bool(if_row)
    if if_row:
        out["item_facts"] = {
            "issue_slug": if_row["issue_slug"],
            "date_end": if_row["date_end"],
            "owner_team": if_row["owner_team"],
            "stype": if_row["stype"],
            "primary_name": if_row["primary_name"],
            "snip": _snip(if_row["snip"], 120),
            "toks_has_play": bool(
                re.search(r"播放|点赞|视频号|片子", (if_row["toks"] or "") + " " + (if_row["snip"] or ""))
            ),
        }

    # item_facts_fts (may not exist on sqlite)
    try:
        iff = _row(
            con,
            "SELECT item_id, issue_slug, substr(text_snippet,1,120) AS snip, toks "
            "FROM item_facts_fts WHERE item_id=? LIMIT 1",
            (iid,),
        )
        out["in_item_facts_fts"] = bool(iff)
        if iff:
            out["item_facts_fts_snip"] = _snip(iff["snip"], 100)
    except Exception as e:
        out["in_item_facts_fts"] = None
        out["item_facts_fts_error"] = str(e)[:80]

    # chunk_index (retrieval materialization)
    try:
        ch = _row(
            con,
            "SELECT chunk_id, issue_slug, substr(title,1,80) AS title, substr(body,1,80) AS body "
            "FROM chunk_index WHERE item_id=? LIMIT 1",
            (iid,),
        )
        out["in_chunk_index"] = bool(ch)
        if ch:
            out["chunk_index"] = {
                "chunk_id": ch["chunk_id"],
                "issue_slug": ch["issue_slug"],
                "title": _snip(ch["title"], 60),
            }
    except Exception as e:
        out["in_chunk_index"] = None
        out["chunk_index_error"] = str(e)[:80]

    # search_fts: usually no item_id — match by title fragment / body overlap
    in_search = False
    search_hits = []
    try:
        # Prefer title/text fragment match within issue
        needle = (title or "").strip()[:40]
        if needle:
            for r in _rows(
                con,
                "SELECT issue_slug, section, title, substr(body,1,120) AS body, date_end "
                "FROM search_fts WHERE issue_slug=? AND (title LIKE ? OR body LIKE ?) LIMIT 5",
                (issue_slug or SLUG, f"%{needle[:20]}%", f"%{needle[:20]}%"),
            ):
                search_hits.append(
                    {
                        "issue_slug": r["issue_slug"],
                        "section": r["section"],
                        "title": _snip(r["title"], 60),
                        "body": _snip(r["body"], 80),
                        "date_end": r["date_end"],
                    }
                )
        # Also body fingerprint
        fp = re.sub(r"\s+", "", body or "")[:24]
        if fp and not search_hits:
            for r in _rows(
                con,
                "SELECT issue_slug, section, title, substr(body,1,120) AS body, date_end "
                "FROM search_fts WHERE issue_slug=? AND body LIKE ? LIMIT 5",
                (issue_slug or SLUG, f"%{fp[:16]}%"),
            ):
                search_hits.append(
                    {
                        "issue_slug": r["issue_slug"],
                        "section": r["section"],
                        "title": _snip(r["title"], 60),
                        "body": _snip(r["body"], 80),
                        "date_end": r["date_end"],
                    }
                )
        # fallback: any slug by distinctive 《title》
        m2 = re.search(r"《([^》]+)》", body or "")
        if not search_hits and m2:
            for r in _rows(
                con,
                "SELECT issue_slug, section, title, substr(body,1,120) AS body, date_end "
                "FROM search_fts WHERE title LIKE ? OR body LIKE ? LIMIT 5",
                (f"%{m2.group(1)[:16]}%", f"%{m2.group(1)[:16]}%"),
            ):
                search_hits.append(
                    {
                        "issue_slug": r["issue_slug"],
                        "section": r["section"],
                        "title": _snip(r["title"], 60),
                        "body": _snip(r["body"], 80),
                        "date_end": r["date_end"],
                    }
                )
        in_search = bool(search_hits)
    except Exception as e:
        out["search_fts_error"] = str(e)[:120]
    out["in_search_fts"] = in_search
    out["search_fts_hits"] = search_hits[:3]

    # Direct FTS / item_facts search with distinctive tokens from the item
    tokens = []
    for tok in re.findall(r"[\u4e00-\u9fffA-Za-z0-9《》？?]{2,}", body):
        if tok not in tokens and tok not in ("视频号", "播放", "点赞", "分享", "本周", "相对", "其余", "视频", "处于", "前列"):
            tokens.append(tok)
        if len(tokens) >= 4:
            break
    # Prefer quoted video title tokens
    m = re.search(r"《([^》]+)》", body)
    probe_q = m.group(1) if m else (" ".join(tokens[:2]) if tokens else "播放")
    out["probe_query"] = probe_q

    direct = {"fts": False, "item_facts": False, "hybrid_rank": None}
    try:
        from app import search as search_mod
        from app import item_facts as if_mod

        fts = search_mod.fts_search(con, probe_q, slug=issue_slug or SLUG, limit=40)
        for i, h in enumerate(fts or []):
            ht = (h.get("title") or "") + (h.get("body") or "")
            if body[:24] and body[:24] in ht:
                direct["fts"] = True
                direct["fts_rank"] = i + 1
                break
            if m and m.group(1)[:8] in ht:
                direct["fts"] = True
                direct["fts_rank"] = i + 1
                break
        ifhits = if_mod.search(con, probe_q, slug=issue_slug or SLUG, limit=40)
        for i, h in enumerate(ifhits or []):
            if str(h.get("item_id")) == str(iid) or h.get("item_id") == iid:
                direct["item_facts"] = True
                direct["item_facts_rank"] = i + 1
                break
        # also try play-related query
        play_hits = if_mod.search(con, "视频号 播放", slug=issue_slug or SLUG, limit=40)
        play_ids = [str(h.get("item_id")) for h in (play_hits or []) if h.get("item_id") is not None]
        direct["play_query_in_top40"] = str(iid) in play_ids
        if str(iid) in play_ids:
            direct["play_query_rank"] = play_ids.index(str(iid)) + 1
    except Exception as e:
        direct["error"] = str(e)[:160]
    out["direct_searchable"] = direct

    # Scope/filter exclusion check for R14 AskScope
    excluded = []
    if issue_status and issue_status != "published":
        excluded.append(f"issue_status={issue_status}")
    if issue_slug and issue_slug != SLUG:
        excluded.append(f"issue_slug={issue_slug}!=R14_scope({SLUG})")
    if if_row and if_row["issue_slug"] and if_row["issue_slug"] != SLUG:
        excluded.append(f"item_facts.slug={if_row['issue_slug']}")
    out["scope_filter_risks"] = excluded

    # Attribution hint (per item)
    if not out["exists_in_items"]:
        out["attribution_hint"] = "B_missing_items"
    elif not out["in_item_facts"] and not out["in_search_fts"]:
        out["attribution_hint"] = "B_not_in_index"
    elif excluded and any("!=" in x or "status=" in x for x in excluded):
        out["attribution_hint"] = "C_scope_filter"
    elif out["in_item_facts"] or out["in_search_fts"]:
        if direct.get("fts") or direct.get("item_facts"):
            out["attribution_hint"] = "A_indexed_but_query_miss"  # probe works; R14 query may not
        else:
            out["attribution_hint"] = "A_indexed_hard_to_match"  # in index but probe also fails
    else:
        out["attribution_hint"] = "unknown"
    return out


def run_r14_query_probe(con) -> dict:
    """Replicate baseline retrieve for R14 query under explicit:2026-09-08 — read-only."""
    from app.ask_scope import AskScope
    from app import retriever, embeddings

    # disable embed like baseline
    embeddings.embed_one = lambda _t: []  # type: ignore
    embeddings.is_configured = lambda: False  # type: ignore

    row = _row(
        con,
        "SELECT date_start, date_end FROM issues WHERE slug=?",
        (SLUG,),
    )
    df = (row["date_start"] or "").strip() or None if row else None
    dt = (row["date_end"] or "").strip() or None if row else None
    scope = AskScope(channel="harness", slug=SLUG, team="", date_from=df, date_to=dt, role="viewer")
    mode, hits, meta = retriever.retrieve(con, QUERY, scope, intent=None, query_vec=None)
    ids = []
    for h in hits or []:
        iid = h.get("item_id")
        if iid is None:
            continue
        ids.append(str(int(iid)) if str(iid).isdigit() else str(iid))
    want = {str(x) for x in IDS}
    return {
        "mode": mode,
        "date_from": df,
        "date_to": dt,
        "n_hits": len(hits or []),
        "retrieved_item_ids_top20": ids[:20],
        "expected_hit": sorted(want & set(ids[:20])),
        "expected_missed": sorted(want - set(ids[:20])),
        "used_vector": bool((meta or {}).get("used_vector")),
    }


def main() -> int:
    from app import db

    con = db.connect()
    report = {
        "id": "R14",
        "expected": IDS,
        "query": QUERY,
        "scope_issue": f"explicit:{SLUG}",
        "items": [],
        "r14_retrieve_probe": None,
    }
    try:
        for iid in IDS:
            report["items"].append(check_one(con, iid))
        try:
            report["r14_retrieve_probe"] = run_r14_query_probe(con)
        except Exception as e:
            report["r14_retrieve_probe"] = {"error": str(e)}
    finally:
        try:
            con.close()
        except Exception:
            pass

    # Aggregate conclusion
    hints = [it.get("attribution_hint") for it in report["items"]]
    if all(h and h.startswith("B_") for h in hints):
        conclusion = "B"
        conclusion_text = "根本没进索引 → Data/Index"
    elif any(h == "C_scope_filter" for h in hints):
        conclusion = "C"
        conclusion_text = "数据存在但被当前 scope/filter 排除 → Scope/Filter"
    elif any(h and h.startswith("A_") for h in hints):
        # if mix of A and B, note it
        if any(h and h.startswith("B_") for h in hints):
            conclusion = "MIX"
            conclusion_text = "部分进索引部分未进；逐条见 items"
        else:
            conclusion = "A"
            conclusion_text = "已进索引，但检索没召回 → FTS/Query"
    else:
        conclusion = "?"
        conclusion_text = "无法统一归因，见逐条"
    report["conclusion"] = conclusion
    report["conclusion_text"] = conclusion_text

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    out_path = Path("/tmp/R14_INDEX_CHECK.json")
    try:
        out_path.write_text(text, encoding="utf-8")
        print(f"\nWROTE {out_path}", file=sys.stderr)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
