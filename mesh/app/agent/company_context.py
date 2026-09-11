"""Company Understanding — Ontology + Wiki +（引用）Grounding 提示，同一 Context。"""
from __future__ import annotations

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
    ont = build_ontology(
        identity=identity,
        permission=permission,
        context=context,
        org_snapshot=org_snapshot,
    )
    wiki = load_wiki(query=user_text or "")
    return CompanyUnderstanding(
        ontology=ont,
        wiki=wiki,
        session_summary=_session_summary(session),
    )
