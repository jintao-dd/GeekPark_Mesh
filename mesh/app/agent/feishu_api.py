"""飞书 Open API 薄封装：tenant_access_token + 发消息 + 更新卡片。

只做通道，不碰 Agent 大脑。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

import requests

log = logging.getLogger("mesh.feishu_api")

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}


def feishu_app_credentials() -> tuple[str, str]:
    app_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    return app_id, app_secret


def bot_reply_enabled() -> bool:
    """默认：有 App 凭证即开启出站；FEISHU_BOT_REPLY=0 可关。"""
    flag = (os.environ.get("FEISHU_BOT_REPLY") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    app_id, app_secret = feishu_app_credentials()
    return bool(app_id and app_secret)


def get_tenant_access_token(*, force: bool = False) -> str:
    app_id, app_secret = feishu_app_credentials()
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID/SECRET missing")
    now = time.time()
    with _TOKEN_LOCK:
        if not force and _TOKEN_CACHE["token"] and now < float(_TOKEN_CACHE["expires_at"]) - 60:
            return str(_TOKEN_CACHE["token"])
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if int(data.get("code") or 0) != 0:
            raise RuntimeError(f"tenant_access_token failed: {data}")
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise RuntimeError(f"tenant_access_token empty: {data}")
        expire = float(data.get("expire") or 7200)
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = now + expire
        return token


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_tenant_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def send_message(
    *,
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content: dict[str, Any] | str,
) -> dict[str, Any]:
    """POST /open-apis/im/v1/messages"""
    rid = (receive_id or "").strip()
    if not rid:
        raise ValueError("receive_id required")
    body_content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    payload = {
        "receive_id": rid,
        "msg_type": msg_type,
        "content": body_content,
    }
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=20)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or int(data.get("code") or 0) != 0:
        log.warning("feishu send_message failed status=%s body=%s", r.status_code, str(data)[:400])
        raise RuntimeError(f"feishu send_message failed: {data or r.text[:300]}")
    return data.get("data") or {}


def patch_message(*, message_id: str, content: dict[str, Any] | str) -> dict[str, Any]:
    """PATCH /open-apis/im/v1/messages/:message_id （更新 interactive 卡片）"""
    mid = (message_id or "").strip()
    if not mid:
        raise ValueError("message_id required")
    body_content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{mid}"
    r = requests.patch(url, headers=_auth_headers(), json={"content": body_content}, timeout=20)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or int(data.get("code") or 0) != 0:
        log.warning("feishu patch_message failed status=%s body=%s", r.status_code, str(data)[:400])
        raise RuntimeError(f"feishu patch_message failed: {data or r.text[:300]}")
    return data.get("data") or data
