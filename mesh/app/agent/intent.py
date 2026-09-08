"""规则 Intent：不可靠 → refuse。禁止 LLM 自由选 Tool。"""
from __future__ import annotations

import re

from .models import AgentContext, PermissionDecision
from .permission import tool_allowed

_HELP = re.compile(
    r"(帮助|怎么用|你能做什么|使用说明|\bhelp\b|\bcommands?\b)",
    re.I,
)
_LIST = re.compile(
    r"(有哪些期|有哪些.{0,10}期次|哪些周报|列出.*期|期次列表|list\s*issues?|有哪几期)",
    re.I,
)
_REL = re.compile(
    r"(关系|对接|双边|交叉|两边|交集|谁见了谁|联动|relations?)",
    re.I,
)
# 「接触」单独成词且像关系问法时再进 relations（避免「商业化接触了谁」误路由）
_REL_CONTACT = re.compile(
    r"(谁接触了谁|两边.*接触|接触.*交集|接触关系)",
    re.I,
)
_DRAFT_RAW = re.compile(
    r"(草稿|draft|原文|raw\s*source|未上线|unpublished|sources?\s*表|直接查库)",
    re.I,
)
_ASKISH = re.compile(r".{2,}", re.S)


def rule_classify_intent(
    text: str,
    context: AgentContext,
    permission: PermissionDecision,
) -> str:
    """优先级：refuse > help > list_issues > ask_relations > ask_published。"""
    q = (text or "").strip()
    if not q:
        return "refuse"

    # 越权探测：明确要 draft/raw → refuse（即使后面会被 Tool 再拦）
    if _DRAFT_RAW.search(q) and not _HELP.search(q):
        # 「帮我看草稿」类
        if re.search(r"(看|读|查|打开|给我|导出).*(草稿|draft|原文|raw|未上线)", q, re.I) or \
           re.search(r"(草稿|draft|原文|raw|未上线).*(内容|全文|json)", q, re.I) or \
           re.search(r"直接查库|查\s*sources", q, re.I):
            return "refuse"

    if _HELP.search(q):
        return "help"

    if _LIST.search(q):
        if tool_allowed(permission, "context.list_issues"):
            return "list_issues"
        return "refuse"

    if _REL.search(q) or _REL_CONTACT.search(q):
        if tool_allowed(permission, "ask.relations_summary"):
            return "ask_relations"
        return "refuse"

    # 普通问答：需数据 Tool ACL
    if _ASKISH.match(q):
        if tool_allowed(permission, "ask.published"):
            return "ask_published"
        return "refuse"

    return "refuse"


def intent_to_tool(intent: str) -> str | None:
    return {
        "help": "system.help",
        "list_issues": "context.list_issues",
        "ask_relations": "ask.relations_summary",
        "ask_published": "ask.published",
        "refuse": None,
    }.get(intent)
