"""Hands 后端：MCP 为主，CLI 为辅；openapi 仅作可注入过渡，不扩成 Tool 动物园。"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Any, Callable, Protocol

import requests

from . import flags
from .normalize import envelope_fail, envelope_ok, normalize_docs
from ..tool_contract import FEISHU_SEARCH, ToolResultEnvelope

log = logging.getLogger("mesh.feishu_hands")

SearchFn = Callable[..., ToolResultEnvelope]


class SearchBackend(Protocol):
    def search_docs(
        self,
        query: str,
        *,
        max_results: int,
        timeout_sec: float,
        user_access_token: str = "",
        open_id: str = "",
    ) -> ToolResultEnvelope: ...


_injected: SearchFn | None = None


def set_search_backend(fn: SearchFn | None) -> None:
    """单测 / 本地注入；生产默认走 env 选型。"""
    global _injected
    _injected = fn


def clear_search_backend() -> None:
    set_search_backend(None)


class McpBackend:
    """HTTP JSON 桥到官方 MCP 侧车（极薄）。未配置 URL → 诚实失败。"""

    def search_docs(
        self,
        query: str,
        *,
        max_results: int,
        timeout_sec: float,
        user_access_token: str = "",
        open_id: str = "",
    ) -> ToolResultEnvelope:
        url = flags.mcp_endpoint()
        if not url:
            return envelope_fail("mcp_not_configured")
        payload = {
            "tool": "feishu.search",
            "arguments": {
                "query": query,
                "resource_type": "doc",
                "max_results": max_results,
            },
            "open_id": open_id or "",
        }
        headers = {"Content-Type": "application/json"}
        if user_access_token:
            headers["X-Feishu-User-Token"] = user_access_token
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout_sec)
            if r.status_code >= 400:
                return envelope_fail(f"mcp_http_{r.status_code}")
            data = r.json() if r.content else {}
            if not isinstance(data, dict):
                return envelope_fail("mcp_bad_payload")
            if data.get("ok") is False:
                return envelope_fail(str(data.get("error") or "mcp_error"))
            items = data.get("items") or data.get("docs_entities") or []
            if not isinstance(items, list):
                items = []
            return envelope_ok(normalize_docs(items))
        except requests.Timeout:
            return envelope_fail("mcp_timeout")
        except Exception as e:
            log.warning("mcp search failed: %s", e)
            return envelope_fail(f"mcp_error:{type(e).__name__}")


class OpenApiDocsBackend:
    """官方「搜索云文档」单接口过渡（需 user_access_token，禁止 tenant 抬权）。"""

    ENDPOINT = "https://open.feishu.cn/open-apis/suite/docs-api/search/object"

    def search_docs(
        self,
        query: str,
        *,
        max_results: int,
        timeout_sec: float,
        user_access_token: str = "",
        open_id: str = "",
    ) -> ToolResultEnvelope:
        token = (user_access_token or "").strip()
        if not token:
            return envelope_fail("user_token_required")
        body = {
            "search_key": query,
            "count": max(1, min(int(max_results), 50)),
            "offset": 0,
            "docs_types": ["doc", "docx", "sheet", "bitable", "file"],
        }
        try:
            r = requests.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=body,
                timeout=timeout_sec,
            )
            data = r.json() if r.content else {}
            if r.status_code >= 400 or int(data.get("code") or 0) != 0:
                return envelope_fail(f"openapi_{data.get('code') or r.status_code}")
            entities = (data.get("data") or {}).get("docs_entities") or []
            if not isinstance(entities, list):
                entities = []
            return envelope_ok(normalize_docs(entities))
        except requests.Timeout:
            return envelope_fail("openapi_timeout")
        except Exception as e:
            log.warning("openapi search failed: %s", e)
            return envelope_fail(f"openapi_error:{type(e).__name__}")


class CliBackend:
    """CLI 辅助：开发/运维；运行时默认不走。"""

    def search_docs(
        self,
        query: str,
        *,
        max_results: int,
        timeout_sec: float,
        user_access_token: str = "",
        open_id: str = "",
    ) -> ToolResultEnvelope:
        bin_path = flags.cli_bin()
        if not bin_path:
            return envelope_fail("cli_not_configured")
        try:
            proc = subprocess.run(
                [
                    bin_path,
                    "docs",
                    "search",
                    "--query",
                    query,
                    "--limit",
                    str(max_results),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if proc.returncode != 0:
                return envelope_fail(f"cli_exit_{proc.returncode}")
            data = json.loads(proc.stdout or "{}")
            items = data if isinstance(data, list) else (data.get("items") or [])
            return envelope_ok(normalize_docs(items if isinstance(items, list) else []))
        except subprocess.TimeoutExpired:
            return envelope_fail("cli_timeout")
        except Exception as e:
            return envelope_fail(f"cli_error:{type(e).__name__}")


def resolve_backend() -> SearchBackend:
    name = flags.backend_name()
    if name == "openapi":
        return OpenApiDocsBackend()
    if name == "cli":
        return CliBackend()
    return McpBackend()


def run_search(
    query: str,
    *,
    max_results: int | None = None,
    timeout_sec: float | None = None,
    user_access_token: str = "",
    open_id: str = "",
) -> ToolResultEnvelope:
    mr = int(max_results if max_results is not None else FEISHU_SEARCH.max_results)
    to = float(timeout_sec if timeout_sec is not None else FEISHU_SEARCH.timeout_sec)
    if _injected is not None:
        return _injected(
            query,
            max_results=mr,
            timeout_sec=to,
            user_access_token=user_access_token,
            open_id=open_id,
        )
    return resolve_backend().search_docs(
        query,
        max_results=mr,
        timeout_sec=to,
        user_access_token=user_access_token,
        open_id=open_id,
    )
