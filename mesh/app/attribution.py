"""条目归属（Attribution）语义与解析。

owner_team 必须有 provenance；LLM / source_label 不得直接决定正式归属。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ingest
from .aggregator import AGG_TEAM, INVALID_OWNER_TEAMS, sanitize_owner_team

# provenance 枚举（落库 owner_provenance）
PROVENANCE_MANUAL = "manual"
PROVENANCE_SEGMENT = "segment_rule"
PROVENANCE_SOURCE = "source"
PROVENANCE_LLM_HINT = "llm_hint"
PROVENANCE_UNKNOWN = "unknown"

VALID_PROVENANCES = frozenset({
    PROVENANCE_MANUAL,
    PROVENANCE_SEGMENT,
    PROVENANCE_SOURCE,
    PROVENANCE_LLM_HINT,
    PROVENANCE_UNKNOWN,
})

# publish 允许的 provenance（llm_hint / unknown 不得上线）
PUBLISHABLE_PROVENANCES = frozenset({
    PROVENANCE_MANUAL,
    PROVENANCE_SEGMENT,
    PROVENANCE_SOURCE,
})


def is_placeholder_pick(pick: str) -> bool:
    """上传页 team 选项是否为占位（不可作正式 owner）。"""
    p = (pick or "").strip()
    if not p or p in INVALID_OWNER_TEAMS or p == AGG_TEAM:
        return True
    owner = sanitize_owner_team(ingest.owner_team_for_pick(p))
    return not owner


def manual_owner_from_source(source_team: str) -> str | None:
    """上传页人工选定 team → 正式 owner（最高权威）。"""
    pick = ingest.source_pick_for(source_team or "")
    if is_placeholder_pick(pick):
        return None
    return sanitize_owner_team(ingest.owner_team_for_pick(pick))


def llm_hint_from_item(it: dict) -> str | None:
    """LLM 产出仅作 hint，永不直接成为 owner_team。"""
    hint = sanitize_owner_team(it.get("llm_owner_team_hint"))
    if hint:
        return hint
    # 兼容旧抽取路径（迁移期读 owner_team 字段作 hint，resolve 仍不会采纳为正式 owner）
    return sanitize_owner_team(it.get("owner_team"))


@dataclass(frozen=True)
class AttributionResult:
    owner_team: str | None
    provenance: str
    llm_owner_team_hint: str | None = None
    needs_review: bool = False
    block: bool = False


def resolve_attribution(
    it: dict,
    *,
    source_team: str,
    segment_team: str | None = None,
) -> AttributionResult:
    """解析条目正式 owner_team 及 provenance。

    优先级：
    1. manual — 上传页非占位 team（无条件覆盖 segment / LLM）
    2. segment_rule — T13 确定性段归属（仅当无 manual）
    3. source — sources.team 映射的非占位 owner（兜底）
    4. llm_hint — 仅记录 hint，needs_review，不得写 owner
    5. unknown — 无归属，block + needs_review

    source_label 不参与判定。
    """
    llm_hint = llm_hint_from_item(it)
    seg = sanitize_owner_team(segment_team) or sanitize_owner_team(it.get("_segment_team"))

    manual = manual_owner_from_source(source_team)
    if manual:
        return AttributionResult(
            owner_team=manual,
            provenance=PROVENANCE_MANUAL,
            llm_owner_team_hint=llm_hint if llm_hint and llm_hint != manual else llm_hint,
        )

    if seg:
        return AttributionResult(
            owner_team=seg,
            provenance=PROVENANCE_SEGMENT,
            llm_owner_team_hint=llm_hint if llm_hint and llm_hint != seg else llm_hint,
        )

    src_owner = sanitize_owner_team(ingest.owner_team_for_pick(source_team))
    if src_owner and src_owner not in INVALID_OWNER_TEAMS:
        return AttributionResult(
            owner_team=src_owner,
            provenance=PROVENANCE_SOURCE,
            llm_owner_team_hint=llm_hint if llm_hint and llm_hint != src_owner else llm_hint,
        )

    if llm_hint:
        return AttributionResult(
            owner_team=None,
            provenance=PROVENANCE_LLM_HINT,
            llm_owner_team_hint=llm_hint,
            needs_review=True,
            block=True,
        )

    return AttributionResult(
        owner_team=None,
        provenance=PROVENANCE_UNKNOWN,
        needs_review=True,
        block=True,
    )


def resolve_item_owner(
    it: dict,
    *,
    source_team: str,
    segment_team: str | None = None,
) -> str | None:
    """兼容旧调用：仅返回 owner_team。"""
    return resolve_attribution(it, source_team=source_team, segment_team=segment_team).owner_team


def apply_attribution_to_item(
    it: dict,
    *,
    source_team: str,
    segment_team: str | None = None,
) -> AttributionResult:
    """写入 item 归属字段并必要时 block。"""
    attr = resolve_attribution(it, source_team=source_team, segment_team=segment_team)
    it["owner_team"] = attr.owner_team
    it["owner_provenance"] = attr.provenance
    if attr.llm_owner_team_hint:
        it["llm_owner_team_hint"] = attr.llm_owner_team_hint
    if attr.block and not attr.owner_team:
        it["blocked"] = 1
        it["_owner_needs_review"] = True
    elif attr.needs_review and not attr.owner_team:
        it["_owner_needs_review"] = True
    return attr
