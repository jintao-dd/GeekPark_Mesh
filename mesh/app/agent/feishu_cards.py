"""飞书 Interactive Card 组装（思考中占位 → 最终回答）。

不改 Agent 大脑；展示文案仍走 feishu_reply.format_display_text。
语气偏口语，避免「状态 / 你的问题」填表感。
"""
from __future__ import annotations

from typing import Any


def _clip(text: str, n: int = 6000) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 20] + "\n…（已截断）"


def _quote_line(query: str) -> str:
    q = _clip(query, 120)
    if not q:
        return ""
    # 飞书 lark_md 用引用弱化回显，不写「你的问题：」标签
    return f"> {q}"


def thinking_card(*, query: str = "", stage: int = 0) -> dict[str, Any]:
    """占位卡。stage: 0 刚收到 / 1 仍在查。"""
    if stage <= 0:
        title = "Mesh"
        body = "收到，我去翻翻最近的周报和证据…\n一般十几秒。"
    else:
        title = "Mesh"
        body = "还在检索，马上好…"

    q = _quote_line(query)
    content = f"{body}\n\n{q}" if q else body
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "wathet",
            "title": {"tag": "plain_text", "content": title[:40]},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content},
            }
        ],
    }


def answer_card(
    *,
    display_text: str,
    title: str = "Mesh",
    template: str = "green",
    query: str = "",
) -> dict[str, Any]:
    # 用户消息已在聊天流里，终卡不再回显「问：」
    _ = query
    body = _clip(display_text, 6000) or "这期没捞到可引用的证据。"
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": (title or "Mesh")[:40]},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body},
            }
        ],
    }


def error_card(*, message: str, query: str = "") -> dict[str, Any]:
    detail = _clip(message or "未知错误", 400)
    return answer_card(
        display_text=f"这次没答上来。\n\n`{detail}`\n\n你可以换个问法再试一次。",
        title="Mesh",
        template="orange",
        query=query,
    )
