"""飞书 Interactive Card 组装（思考中占位 → 最终回答）。

不改 Agent 大脑；展示文案仍走 feishu_reply.format_display_text。
"""
from __future__ import annotations

from typing import Any


def _clip(text: str, n: int = 6000) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 20] + "\n…（已截断）"


def thinking_card(*, query: str = "", status: str = "检索证据中…") -> dict[str, Any]:
    q = _clip(query, 200)
    lines = [
        f"**状态：** {status}",
        "",
        "Mesh 正在检索已上线周报与证据，通常需要约 10–20 秒。",
    ]
    if q:
        lines.extend(["", f"**你的问题：** {q}"])
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "Mesh · 思考中"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(lines)},
            }
        ],
    }


def answer_card(
    *,
    display_text: str,
    title: str = "Mesh · 回答",
    template: str = "green",
    query: str = "",
) -> dict[str, Any]:
    body = _clip(display_text, 6000)
    parts: list[str] = []
    if query:
        parts.append(f"**问：** {_clip(query, 200)}")
        parts.append("")
    parts.append(body or "（空回答）")
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title[:40] or "Mesh · 回答"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(parts)},
            }
        ],
    }


def error_card(*, message: str, query: str = "") -> dict[str, Any]:
    return answer_card(
        display_text=f"**出错了**\n{message or '未知错误'}",
        title="Mesh · 失败",
        template="red",
        query=query,
    )
