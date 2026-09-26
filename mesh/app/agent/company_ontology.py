"""Company Ontology v0 — 结构先验（谁 / 哪队 / 对象世界）。

从 Identity + Permission + Org 原料反推；不是 Neo4j，不改 Recall。
source_tier=ontology · truth_level=company_structure
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CompanyOntology:
    """只读结构包，注入 Brain / Judge / Specialists。"""

    company: str = "GeekPark（极客公园）"
    open_id: str = ""
    display_name: str = ""
    primary_team: str = ""
    mapped_teams: list[str] = field(default_factory=list)
    identity_status: str = ""
    permission_mode: str = ""
    team_focus: str = ""
    published_only: bool = True
    entity_types: list[str] = field(
        default_factory=lambda: ["person", "team", "product", "project", "issue"]
    )
    relation_types: list[str] = field(
        default_factory=lambda: [
            "member_of",
            "works_with",
            "owns",
            "mentioned_in_issue",
        ]
    )
    org_notes: list[str] = field(default_factory=list)
    source_tier: str = "ontology"
    truth_level: str = "company_structure"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_block(self) -> str:
        teams = "、".join(self.mapped_teams[:8]) if self.mapped_teams else "（未映射）"
        lines = [
            "## 公司结构先验（Ontology · 非企业事实）",
            f"- 公司：{self.company}",
            f"- 你在跟谁说话：{self.display_name or '同事'}（open_id={self.open_id or '未知'}）",
            f"- 身份状态：{self.identity_status or 'unknown'}",
            f"- 主团队：{self.primary_team or '未知'}",
            f"- 映射团队：{teams}",
            f"- 查询焦点团队：{self.team_focus or '无'}",
            f"- 权限模式：{self.permission_mode or 'unknown'}；published_only={self.published_only}",
            f"- 实体类型：{', '.join(self.entity_types)}",
            f"- 关系粗类：{', '.join(self.relation_types)}",
        ]
        for n in self.org_notes[:6]:
            lines.append(f"- 注：{n}")
        lines.append(
            "硬规则：Ontology 只解释结构/归属，禁止当成「今天发生了什么」的事实断言。"
        )
        return "\n".join(lines)


def build_ontology(
    *,
    identity: Any = None,
    permission: Any = None,
    context: Any = None,
    org_snapshot: dict[str, Any] | None = None,
) -> CompanyOntology:
    oid = str(getattr(identity, "feishu_open_id", None) or "").strip()
    name = str(
        getattr(identity, "display_hint", None)
        or getattr(identity, "person", None)
        or ""
    ).strip()
    if isinstance(getattr(identity, "person", None), dict):
        name = str(
            (identity.person or {}).get("name")
            or (identity.person or {}).get("display_name")
            or name
        ).strip()
    primary = str(getattr(identity, "primary_team", None) or "").strip()
    mapped = [
        str(t).strip()
        for t in (getattr(identity, "mapped_teams", None) or [])
        if str(t).strip()
    ]
    status = str(getattr(identity, "status", None) or "").strip()
    qs = getattr(permission, "query_scope", None) or {}
    if not isinstance(qs, dict):
        qs = {}
    mode = str(qs.get("mode") or "").strip()
    focus = str(qs.get("team_focus") or getattr(context, "chat_team", None) or "").strip()
    vis = getattr(permission, "data_visibility", None) or {}
    published_only = True
    if isinstance(vis, dict):
        published_only = bool(vis.get("published_only", True))
    elif hasattr(permission, "published_only"):
        published_only = bool(getattr(permission, "published_only"))

    notes: list[str] = []
    if org_snapshot and isinstance(org_snapshot, dict):
        n_people = int(org_snapshot.get("people_count") or 0)
        n_dept = int(org_snapshot.get("department_count") or 0)
        if n_people or n_dept:
            notes.append(f"通讯录缓存约 {n_dept} 部门 / {n_people} 人（应用可见范围）")
        teammates = org_snapshot.get("teammates") or []
        team = str(org_snapshot.get("team") or "").strip()
        if isinstance(teammates, list) and teammates:
            shown = "、".join(str(x).strip() for x in teammates[:16] if str(x).strip())
            if shown:
                notes.append(
                    f"飞书子树同事（{team or '本队'}，wiki_prior）：{shown}"
                )
    try:
        from .dept_team_map import brand_creative_tree_note

        notes.append(brand_creative_tree_note())
        notes.append(
            "飞书树 ≠ 周报桶，但「我们团队」统一认 Mesh 业务队（primary_team）。"
            "品牌创意下创新技术/创意视频/品牌设计不再按叶子切「我们团队」。"
            "海外拓展为独立业务队；问品牌创意时仍可按 parent_team 纳入其人。"
            "材料里出现业务队同事即算相关，禁止因周报桶名不同而排除。"
        )
    except Exception:
        pass
    notes.append(
        "硅谷对外人脉在 Notion CRM（Owner/Our side=Lilyann=思琪）。"
        "对外人脉/BD 跟进走 crm.search；内部同事走飞书通讯录。"
    )
    if not oid:
        notes.append("未绑定飞书 open_id：个人日历/全库搜等需授权后才完整")
    if not primary and not mapped:
        notes.append("团队映射为空：结构消歧能力弱，问「哪队」时要诚实说不确定")

    return CompanyOntology(
        open_id=oid,
        display_name=name,
        primary_team=primary,
        mapped_teams=mapped,
        identity_status=status,
        permission_mode=mode,
        team_focus=focus,
        published_only=published_only,
        org_notes=notes,
    )
