"""Hands 开关。默认全关，避免未接 MCP 时误伤生产。"""
from __future__ import annotations

import os


def _flag(name: str, default: str = "0") -> bool:
    v = (os.environ.get(name) or default).strip().lower()
    return v not in ("0", "false", "no", "off", "")


def hands_enabled() -> bool:
    """MESH_FEISHU_HANDS=1 才开放 feishu.search 等读工具。"""
    return _flag("MESH_FEISHU_HANDS", "0")


def write_enabled() -> bool:
    """写操作双闸：Hands 开 + MESH_FEISHU_HANDS_WRITE=1。"""
    return hands_enabled() and _flag("MESH_FEISHU_HANDS_WRITE", "0")


def backend_name() -> str:
    """native | mcp | openapi | cli | mock — 默认 native（真实 OpenAPI）。"""
    return (os.environ.get("MESH_FEISHU_HANDS_BACKEND") or "native").strip().lower()


def mcp_endpoint() -> str:
    return (os.environ.get("MESH_FEISHU_HANDS_MCP_URL") or "").strip()


def cli_bin() -> str:
    return (os.environ.get("MESH_FEISHU_CLI_BIN") or "").strip()
