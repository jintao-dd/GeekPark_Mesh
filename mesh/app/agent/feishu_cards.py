"""飞书 Interactive / CardKit 2.0 卡片组装。

A：阶段态等待文案
B1：终答假流式（全量文本交给飞书打字机）
不改 Agent 大脑。
"""
from __future__ import annotations

import re
from typing import Any


BODY_ELEMENT_ID = "mesh_body"
ACTIONS_ELEMENT_ID = "mesh_actions"


def _clip(text: str, n: int = 6000) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 20] + "\n…（已截断）"


def stage_copy(stage: int, *, query: str = "") -> str:
    """等待阶段文案（口语）。不默认假装「正在查周报」。"""
    q = _clip(query, 80)
    quote = f"\n\n> {q}" if q else ""
    # 明显写稿/闲聊：别说翻周报
    soft = bool(
        re.search(
            r"(写|文章|润色|改写|翻译|帮忙看看|忙死了|哈哈|傻|机械|同事)",
            query or "",
        )
    )
    if soft:
        if stage <= 0:
            return f"嗯，我想一下…{quote}"
        return f"差不多了…{quote}"
    if stage <= 0:
        return f"收到，我看一下…{quote}"
    if stage == 1:
        return f"若要查周报，我在已上线内容里找证据…{quote}"
    return f"在整理，马上好…{quote}"


def followup_suggestions(query: str = "", *, display_text: str = "") -> list[str]:
    """仅在业务答得上时给轻量续问；meta/闲聊/没查到不加「还可以问」。"""
    body = display_text or ""
    if any(
        k in body
        for k in (
            "没找到",
            "没查到",
            "不太确定",
            "我是 Mesh",
            "不客气",
            "哈哈",
            "好。",
            "我在。",
            "换个说法",
            "说名字",
        )
    ):
        return []
    q = (query or "").strip()
    out: list[str] = []
    if re.search(r"硅谷|湾区|SF|San\s*Francisco", q, re.I):
        out.append("硅谷还有别的触点吗？")
    if re.search(r"谁|哪些人|沟通|接触", q):
        out.append("还有别人吗？")
    for name in ("小鹏", "高德", "英伟达", "阿里"):
        if name in q:
            out.append(f"那{name}后来怎么样了？")
            break
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s and s not in seen and s != q:
            seen.add(s)
            uniq.append(s)
    return uniq[:2]


def streaming_config() -> dict[str, Any]:
    # 快打字机：约 30ms × ceil(n/20)，长答也不拖
    return {
        "print_frequency_ms": {"default": 30, "android": 30, "ios": 30, "pc": 30},
        "print_step": {"default": 20, "android": 20, "ios": 20, "pc": 20},
        "print_strategy": "fast",
    }


def card_json_v2(
    *,
    title: str,
    body_md: str,
    template: str = "wathet",
    streaming: bool = True,
    followups: list[str] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    """卡片 JSON 2.0（CardKit）。"""
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": _clip(body_md, 10000) or " ",
            "element_id": BODY_ELEMENT_ID,
        }
    ]
    tips = list(followups or [])
    if tips:
        # CardKit schema 2.0 不再支持 tag=action；改用 markdown 列表避免整卡更新失败
        tip_lines = "\n".join(f"- {t[:80]}" for t in tips[:3] if str(t).strip())
        if tip_lines:
            elements.append(
                {
                    "tag": "markdown",
                    "content": f"\n你可以接着问：\n{tip_lines}",
                    "element_id": ACTIONS_ELEMENT_ID,
                }
            )

    cfg: dict[str, Any] = {
        "wide_screen_mode": True,
        "update_multi": True,
        "streaming_mode": bool(streaming),
        "summary": {"content": summary or ("[生成中…]" if streaming else _clip(body_md, 40))},
    }
    if streaming:
        cfg["streaming_config"] = streaming_config()

    return {
        "schema": "2.0",
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": (title or "Mesh")[:40]},
        },
        "config": cfg,
        "body": {"elements": elements},
    }


def thinking_card_v2(*, query: str = "", stage: int = 0) -> dict[str, Any]:
    return card_json_v2(
        title="Mesh",
        body_md=stage_copy(stage, query=query),
        template="wathet",
        streaming=True,
        summary="Mesh 检索中…",
    )


def answer_card_v2(
    *,
    display_text: str,
    query: str = "",
    streaming: bool = False,
) -> dict[str, Any]:
    return card_json_v2(
        title="Mesh",
        body_md=_clip(display_text, 10000) or "这期没捞到可引用的证据。",
        template="green",
        streaming=streaming,
        followups=followup_suggestions(query, display_text=display_text),
        summary=_clip(display_text, 36) or "Mesh 回答",
    )


def error_card_v2(*, message: str, query: str = "") -> dict[str, Any]:
    detail = _clip(message or "未知错误", 400)
    return card_json_v2(
        title="Mesh",
        body_md=f"这次没答上来。\n\n`{detail}`\n\n你可以换个问法再试一次。",
        template="orange",
        streaming=False,
        followups=followup_suggestions(query),
        summary="Mesh 出错了",
    )


# —— 旧版 interactive（无 CardKit 权限时回退）——


def thinking_card(*, query: str = "", stage: int = 0) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "wathet",
            "title": {"tag": "plain_text", "content": "Mesh"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": stage_copy(stage, query=query)},
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
    body = _clip(display_text, 6000) or "这期没捞到可引用的证据。"
    tips = followup_suggestions(query, display_text=display_text)
    if tips:
        body = body + "\n\n还可以问：\n" + "\n".join(f"· {t}" for t in tips)
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


def estimate_typewriter_seconds(text: str) -> float:
    """粗估打字机上屏秒数（与 streaming_config 对齐）。"""
    n = max(1, len(text or ""))
    step = 20
    freq_ms = 30
    return min(1.2, max(0.15, (n / step) * (freq_ms / 1000.0)))


def fake_stream_prefixes(text: str, *, min_chunk: int = 48) -> list[str]:
    """B1：把终答切成递增前缀，便于多次推送（也兼容旧 patch）。"""
    t = _clip(text, 10000)
    if not t:
        return [" "]
    parts: list[str] = []
    # 优先按段落/句号切
    buf = ""
    for ch in t:
        buf += ch
        if len(buf) >= min_chunk and ch in "。！？\n":
            parts.append(buf)
    if not parts or parts[-1] != t:
        # 固定步长兜底
        parts = []
        step = max(min_chunk, 60)
        for i in range(step, len(t), step):
            parts.append(t[:i])
        parts.append(t)
    # 去重保序，确保最后是全文
    out: list[str] = []
    for p in parts:
        if not out or p != out[-1]:
            out.append(p)
    if out[-1] != t:
        out.append(t)
    return out
