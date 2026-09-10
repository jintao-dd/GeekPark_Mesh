"""Feishu Hands — 极薄适配层。

Brain 只看见 tool_contract；本包对接 MCP（主）/ CLI（辅）/ 可注入后端。
默认关闭；不越权、不混级、失败不编造。
"""
from __future__ import annotations

from .flags import hands_enabled, write_enabled
from .search import search as feishu_search

__all__ = [
    "feishu_search",
    "hands_enabled",
    "write_enabled",
]
