"""Company Understanding — Ontology + Wiki +（引用）Grounding 提示，同一 Context。"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .company_ontology import CompanyOntology, build_ontology
from .company_wiki import CompanyWiki, load_wiki


@dataclass
class CompanyUnderstanding:
    ontology: CompanyOntology
    wiki: CompanyWiki
    grounding_hint: str = (
        "分桶纪律：published=已上线周报事实；feishu_live=飞书现场材料；"
        "wiki_prior=公司先验别名（非事实）；analysis=综合判断。"
        "合成时 FACT 不得跨桶冒充。Wiki/Ontology 不得写入 FACT。"
    )
    session_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology": self.ontology.to_dict(),
            "wiki": self.wiki.to_dict(),
            "grounding_hint": self.grounding_hint,
            "session_summary": self.session_summary,
        }

    def prompt_block(self) -> str:
        parts = [
            self.ontology.prompt_block(),
            "",
            self.wiki.prompt_block(),
            "",
            "## Grounding 纪律",
            self.grounding_hint,
        ]
        if self.session_summary:
            parts.extend(["", "## 会话工作记忆（非事实）", self.session_summary])
        return "\n".join(parts)


# 进程内缓存：company_context 构建（Ontology + Wiki）比较重，但同请求/同用户短期内不变
_CC_CACHE: dict[str, tuple[float, CompanyUnderstanding]] = {}
_CC_CACHE_LOCK = threading.Lock()
_CC_CACHE_TTL_S = 60


def _cc_cache_key(
    *,
    identity: Any = None,
    permission: Any = None,
    context: Any = None,
    session: Any = None,
    user_text: str = "",
    org_snapshot: dict[str, Any] | None = None,
) -> str:
    oid = str(getattr(identity, "feishu_open_id", "") or "").strip()
    primary = str(getattr(identity, "primary_team", "") or "").strip()
    q = (user_text or "").strip()[:200]
    snap = dict(org_snapshot or {})
    blob = json.dumps(
        {
            "oid": oid,
            "primary": primary,
            "query": q,
            "team_focus": str((getattr(permission, "query_scope", None) or {}).get("team_focus") or "").strip(),
            "published_only": bool((getattr(permission, "data_visibility", None) or {}).get("published_only", True)),
            "session_goal": str((getattr(session, "active_goal", None) or {}).get("summary", "")).strip()[:80],
            "session_team": str(getattr(session, "active_team", "") or "").strip()[:40],
            "org_digest": sorted(
                [f"{k}={sorted(v) if isinstance(v, list) else v}" for k, v in snap.items()]
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _session_summary(session: Any) -> str:
    if session is None:
        return ""
    bits: list[str] = []
    goal = getattr(session, "active_goal", None)
    if isinstance(goal, dict) and goal.get("summary"):
        bits.append(f"active_goal: {goal.get('summary')}")
    pending = getattr(session, "pending_write", None)
    if isinstance(pending, dict) and pending.get("tool"):
        bits.append(f"pending_write: {pending.get('tool')}")
    block = getattr(session, "last_block", None)
    if isinstance(block, dict) and block.get("code"):
        bits.append(f"last_block: {block.get('code')}")
    team = str(getattr(session, "active_team", "") or "").strip()
    if team:
        bits.append(f"active_team: {team}")
    return "；".join(bits)


def assemble(
    *,
    identity: Any = None,
    permission: Any = None,
    context: Any = None,
    session: Any = None,
    user_text: str = "",
    org_snapshot: dict[str, Any] | None = None,
) -> CompanyUnderstanding:
    key = _cc_cache_key(
        identity=identity,
        permission=permission,
        context=context,
        session=session,
        user_text=user_text,
        org_snapshot=org_snapshot,
    )
    now = time.monotonic()
    with _CC_CACHE_LOCK:
        ent = _CC_CACHE.get(key)
        if ent and now - ent[0] < _CC_CACHE_TTL_S:
            return ent[1]

    snap = dict(org_snapshot or {})
    # 深化 prior：提问者飞书子树同事名（仍标 wiki_prior，非 FACT）
    if not snap.get("teammates"):
        try:
            team = str(getattr(identity, "primary_team", None) or "").strip()
            if not team and session is not None:
                team = str(getattr(session, "active_team", "") or "").strip()
            if team:
                from .feishu_hands import org_directory as od

                names = od.member_names_for_scope(team, limit=20)
                if names:
                    snap["teammates"] = names
                    snap["team"] = team
        except Exception:
            pass
    ont = build_ontology(
        identity=identity,
        permission=permission,
        context=context,
        org_snapshot=snap or None,
    )
    wiki = load_wiki(query=user_text or "")
    result = CompanyUnderstanding(
        ontology=ont,
        wiki=wiki,
        session_summary=_session_summary(session),
    )
    with _CC_CACHE_LOCK:
        _CC_CACHE[key] = (time.monotonic(), result)
        # 简单 GC：TTL 外淘汰
        stale = [k for k, v in _CC_CACHE.items() if now - v[0] > _CC_CACHE_TTL_S * 2]
        for k in stale:
            _CC_CACHE.pop(k, None)
    return result
