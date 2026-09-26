"""跨期关系指纹：轻量延续，不做知识图谱。

用规范化标题实体 + 排序后的实线团队生成 fingerprint；
在候选上标注 continued_from（上一期 slug/title），供 Decision 提示与审校。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[·•|/／、,，.。:：;；\-—_（）()【】\[\]]+")


def normalize_relation_title(title: str) -> str:
    t = _WS.sub("", (title or "").strip().lower())
    t = _PUNCT.sub("", t)
    return t[:80]


def relation_fingerprint(title: str, teams: list[str] | None = None) -> str:
    base = normalize_relation_title(title)
    team_key = "|".join(sorted({(t or "").strip() for t in (teams or []) if (t or "").strip()}))
    raw = f"{base}::{team_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _teams_from_rel(rel: dict) -> list[str]:
    out: list[str] = []
    for t in rel.get("teams") or []:
        if isinstance(t, str) and t.strip() and not t.strip().startswith("→"):
            out.append(t.strip())
    return out


def load_recent_relation_fingerprints(con, *, before_slug: str = "", limit_issues: int = 8) -> dict[str, dict]:
    """返回 fingerprint -> {slug, title, teams}（最近已发布期）。"""
    rows = con.execute(
        """SELECT slug, published_json, date_end FROM issues
           WHERE status='published' AND published_json IS NOT NULL AND published_json<>''
           ORDER BY date_end DESC LIMIT ?""",
        (max(1, limit_issues),),
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        slug = row["slug"] or ""
        if before_slug and slug == before_slug:
            continue
        try:
            data = json.loads(row["published_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for rel in data.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            title = (rel.get("title") or "").strip()
            if not title:
                continue
            teams = _teams_from_rel(rel)
            fp = relation_fingerprint(title, teams)
            if fp not in out:
                out[fp] = {"slug": slug, "title": title, "teams": teams}
            # 也用仅标题指纹兜底（团队集合变动时仍能提示延续）
            fp2 = relation_fingerprint(title, [])
            if fp2 not in out:
                out[fp2] = {"slug": slug, "title": title, "teams": teams}
    return out


def annotate_candidates_with_continuity(
    candidates: list[dict],
    prior: dict[str, dict],
) -> list[dict]:
    """给候选打上 continued_from / relation_fp（原地改并返回）。"""
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        title = (c.get("candidate_title") or c.get("title") or "").strip()
        teams = []
        for t in c.get("teams") or []:
            if isinstance(t, str) and t.strip() and not t.startswith("→"):
                teams.append(t.strip())
        fp = relation_fingerprint(title, teams)
        c["relation_fp"] = fp
        hit = prior.get(fp) or prior.get(relation_fingerprint(title, []))
        if hit:
            c["continued_from"] = {
                "slug": hit.get("slug"),
                "title": hit.get("title"),
                "teams": hit.get("teams") or [],
            }
    return candidates
