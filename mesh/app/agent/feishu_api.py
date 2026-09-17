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
    code = int(data.get("code") or 0)
    if r.status_code >= 400 or code != 0:
        log.warning("feishu send_message failed status=%s body=%s", r.status_code, str(data)[:400])
        raise FeishuApiError(
            f"feishu send_message failed: {data or r.text[:300]}",
            code=code,
            data=data,
        )
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
    code = int(data.get("code") or 0)
    if r.status_code >= 400 or code != 0:
        log.warning("feishu patch_message failed status=%s body=%s", r.status_code, str(data)[:400])
        raise FeishuApiError(
            f"feishu patch_message failed: {data or r.text[:300]}",
            code=code,
            data=data,
        )
    return data.get("data") or data


# def delete_message(*, message_id: str) -> None:
#     """DELETE /open-apis/im/v1/messages/:message_id — 撤回 Bot 自己发送的消息。"""
#     mid = (message_id or "").strip()
#     if not mid:
#         raise ValueError("message_id required")
#     url = f"https://open.feishu.cn/open-apis/im/v1/messages/{mid}"
#     try:
#         _api_json("DELETE", url)
#     except FeishuApiError as e:
#         # 已被删除或超时也视为可接受
#         if e.code in (230110, 230009, 230026):
#             log.debug("delete_message skipped: %s", e)
#             return
#         raise


class FeishuApiError(RuntimeError):
    """带飞书错误码的 API 异常。"""

    def __init__(self, message: str, code: int = 0, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = int(code or 0)
        self.data = data or {}


def _api_json(method: str, url: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.request(method, url, headers=_auth_headers(), json=payload, timeout=20)
    data = r.json() if r.content else {}
    code = int(data.get("code") or 0)
    if r.status_code >= 400 or code != 0:
        raise FeishuApiError(
            f"feishu {method} {url} failed: {data or r.text[:300]}",
            code=code,
            data=data,
        )
    return data


def create_card_entity(card: dict[str, Any]) -> str:
    """POST /open-apis/cardkit/v1/cards → card_id。需 cardkit:card:write。"""
    payload = {
        "type": "card_json",
        "data": json.dumps(card, ensure_ascii=False),
    }
    data = _api_json("POST", "https://open.feishu.cn/open-apis/cardkit/v1/cards", payload=payload)
    card_id = str((data.get("data") or {}).get("card_id") or "").strip()
    if not card_id:
        raise RuntimeError(f"feishu create_card empty id: {data}")
    return card_id


def send_card_entity(
    *,
    receive_id: str,
    receive_id_type: str,
    card_id: str,
) -> dict[str, Any]:
    """用卡片实体 ID 发 interactive 消息。"""
    content = {"type": "card", "data": {"card_id": card_id}}
    return send_message(
        receive_id=receive_id,
        receive_id_type=receive_id_type,
        msg_type="interactive",
        content=content,
    )


class StreamingModeClosedError(FeishuApiError):
    """CardKit 流式会话已关闭（300309）。"""

    def __init__(self, message: str = "streaming mode is closed", data: dict[str, Any] | None = None):
        super().__init__(message, code=300309, data=data or {})


def stream_card_text(
    *,
    card_id: str,
    element_id: str,
    content: str,
    sequence: int,
) -> None:
    """PUT …/elements/:id/content — 全量文本，前缀增量则打字机。"""
    url = (
        f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}"
        f"/elements/{element_id}/content"
    )
    try:
        _api_json(
            "PUT",
            url,
            payload={
                "content": content if content else " ",
                "sequence": int(sequence),
            },
        )
    except FeishuApiError as e:
        if e.code == 300309:
            raise StreamingModeClosedError(data=e.data) from e
        raise


def update_card_settings(*, card_id: str, settings: dict[str, Any], sequence: int) -> None:
    """PATCH …/cards/:id/settings"""
    url = f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}/settings"
    try:
        _api_json(
            "PATCH",
            url,
            payload={
                "settings": json.dumps(settings, ensure_ascii=False),
                "sequence": int(sequence),
            },
        )
    except FeishuApiError as e:
        if e.code == 300309:
            # settings 接口理论上不会返回 300309，但统一兜底
            raise StreamingModeClosedError(data=e.data) from e
        raise


def update_card_entity(*, card_id: str, card: dict[str, Any], sequence: int) -> None:
    """PUT …/cards/:id 全量更新实体。"""
    url = f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}"
    _api_json(
        "PUT",
        url,
        payload={
            "card": {
                "type": "card_json",
                "data": json.dumps(card, ensure_ascii=False),
            },
            "sequence": int(sequence),
        },
    )


class CardSeq:
    """同一 card_id 上严格递增的 sequence。"""

    def __init__(self, start: int = 1):
        self._n = int(start) - 1
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._n += 1
            return self._n


def cardkit_enabled() -> bool:
    """FEISHU_CARDKIT=0 可关；默认开（失败时业务层回退旧卡）。"""
    flag = (os.environ.get("FEISHU_CARDKIT") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off")
