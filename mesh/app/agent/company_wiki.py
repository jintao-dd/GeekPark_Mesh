"""Company Wiki v0 — 语境先验（黑话 / 惯例 / 项目别名）。

派生视图 · source_tier=wiki_context · 禁止冒充 enterprise_fact。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WikiPage:
    slug: str
    title: str
    body: str
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# GeekPark Mesh 最小种子（可随金丝雀加页；不是百科全书）
_SEED_PAGES: list[WikiPage] = [
    WikiPage(
        slug="mesh",
        title="Mesh",
        aliases=["mesh", "同事 mesh", "geekpark mesh"],
        body=(
            "Mesh 是极客公园内部 AI 同事，跑在飞书里。"
            "企业事实只认已上线周报（Published）；飞书文档/群消息是 live 语境，不能说成周报结论。"
        ),
    ),
    WikiPage(
        slug="weekly",
        title="周报 / Issue",
        aliases=["周报", "已上线周报", "issue", "期次"],
        body=(
            "「周报」通常指 Mesh 里已 Owner 上线的 Issue（published）。"
            "问「最近周报里…」应走 Grounding / ask.published，不是飞书随便一篇文档。"
        ),
    ),
    WikiPage(
        slug="tmesh",
        title="tmesh",
        aliases=["tmesh", "测试环境"],
        body="tmesh（tmesh.geekpark.net）是 Mesh 测试环境；prod 是 mesh.geekpark.ai。发版先 tmesh 再 prod。",
    ),
    WikiPage(
        slug="crs",
        title="CRS",
        aliases=["crs", "CRS"],
        body="CRS 在内部语境里常指内容/评审相关工作流缩写；具体指代以当轮周报与飞书讨论为准，Wiki 只给消歧提示。",
    ),
    WikiPage(
        slug="hands",
        title="Feishu Hands",
        aliases=["hands", "飞书手脚", "写入飞书"],
        body=(
            "Hands = 飞书读写工具运行时。写入必须用户确认 + WRITE 开关。"
            "「我的日历」等个人数据需要 User OAuth（UAT），机器人身份不够。"
        ),
    ),
    WikiPage(
        slug="shangche",
        title="上车",
        aliases=["上车", "量产上车"],
        body=(
            "「上车」在科技报道语境常指量产落地/进入车型；"
            "是否已发生必须以 Published 证据为准，不能因 Wiki 有词就断言完成态。"
        ),
    ),
]


@dataclass
class CompanyWiki:
    pages: list[WikiPage] = field(default_factory=list)
    matched: list[WikiPage] = field(default_factory=list)
    source_tier: str = "wiki_context"
    truth_level: str = "company_context"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_tier": self.source_tier,
            "truth_level": self.truth_level,
            "pages": [p.to_dict() for p in self.pages],
            "matched": [p.to_dict() for p in self.matched],
        }

    def prompt_block(self, *, max_pages: int = 4) -> str:
        use = self.matched or self.pages[:max_pages]
        if not use:
            return "## 公司语境（Wiki）\n（暂无种子页）"
        lines = [
            "## 公司语境先验（Wiki · wiki_context · 非企业事实）",
            "以下只帮助理解说法/惯例；禁止写进 FACT 栏冒充周报结论。",
        ]
        for p in use[:max_pages]:
            lines.append(f"### {p.title}")
            lines.append(p.body)
        return "\n".join(lines)


def load_wiki(*, query: str = "") -> CompanyWiki:
    q = (query or "").strip().lower()
    matched: list[WikiPage] = []
    if q:
        for p in _SEED_PAGES:
            keys = [p.slug, p.title] + list(p.aliases)
            if any(str(k).lower() in q or q in str(k).lower() for k in keys if k):
                matched.append(p)
            elif any(str(k).lower() and str(k).lower() in q for k in keys):
                matched.append(p)
    # 去重保序
    seen: set[str] = set()
    uniq: list[WikiPage] = []
    for p in matched:
        if p.slug in seen:
            continue
        seen.add(p.slug)
        uniq.append(p)
    return CompanyWiki(pages=list(_SEED_PAGES), matched=uniq)


def match_terms(query: str) -> list[str]:
    w = load_wiki(query=query)
    return [p.slug for p in w.matched]
