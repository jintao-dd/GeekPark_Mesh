"""飞书个人授权（user_access_token）存储与引导。

企业 Agent 双身份：
- bot / tenant_access_token：应用通讯录、可见群、drive 搜索、代发消息等
- user / user_access_token：我的日历详情、个人文档检索、邮箱、通讯录关键词搜人等

本模块只负责按 open_id 存取 UAT，并生成 Agent 授权链接（飞书内点开浏览器完成）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger("mesh.feishu_user_auth")
_LOCK = threading.Lock()

# Agent Hands 需要的用户侧 scope（与 Mesh 网页登录最小 scope 分开）
AGENT_USER_SCOPES = (
    "contact:user.base:readonly "
    "contact:user:search "
    "contact:user:readonly "
    "calendar:calendar:readonly "
    "calendar:calendar.event:readonly "
    "drive:drive:readonly "
    "docs:doc:readonly "
    "im:chat:readonly "
    "im:message "
    "offline_access"
).strip()


def _store_dir() -> Path:
    root = Path(os.environ.get("MESH_DATA_DIR") or os.environ.get("MESH_DB_DIR") or "data")
    d = root / "feishu_uat"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path_for(open_id: str) -> Path:
    h = hashlib.sha256((open_id or "").encode("utf-8")).hexdigest()[:40]
    return _store_dir() / f"{h}.json"


def save_user_token(
    open_id: str,
    *,
    access_token: str,
    refresh_token: str = "",
    expires_in: int = 0,
    scopes: str = "",
) -> None:
    oid = (open_id or "").strip()
    tok = (access_token or "").strip()
    if not oid or not tok:
        return
    now = time.time()
    exp = int(expires_in or 0)
    blob = {
        "open_id": oid,
        "access_token": tok,
        "refresh_token": (refresh_token or "").strip(),
        "expires_at": now + max(60, exp - 120) if exp else now + 7000,
        "scopes": (scopes or AGENT_USER_SCOPES).strip(),
        "updated_at": now,
    }
    with _LOCK:
        _path_for(oid).write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    log.info("feishu UAT saved open_id=%s… expires_in=%s", oid[:12], exp)


def load_user_token(open_id: str) -> str:
    oid = (open_id or "").strip()
    if not oid:
        return ""
    path = _path_for(oid)
    with _LOCK:
        if not path.exists():
            return ""
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
    if not isinstance(blob, dict):
        return ""
    tok = str(blob.get("access_token") or "").strip()
    exp = float(blob.get("expires_at") or 0)
    if not tok:
        return ""
    if exp and time.time() > exp:
        refreshed = _try_refresh(oid, str(blob.get("refresh_token") or ""))
        return refreshed
    return tok


def _try_refresh(open_id: str, refresh_token: str) -> str:
    rt = (refresh_token or "").strip()
    if not rt:
        return ""
    try:
        import requests
        from . import feishu_api

        app_id, app_secret = feishu_api.feishu_app_credentials()
        # app_access_token
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        ).json()
        app_tok = str(r.get("app_access_token") or "")
        if not app_tok:
            return ""
        r2 = requests.post(
            "https://open.feishu.cn/open-apis/authen/v1/oidc/refresh_access_token",
            headers={"Authorization": f"Bearer {app_tok}", "Content-Type": "application/json"},
            json={"grant_type": "refresh_token", "refresh_token": rt},
            timeout=10,
        ).json()
        data = r2.get("data") or {}
        at = str(data.get("access_token") or "").strip()
        if not at:
            log.warning("UAT refresh failed open_id=%s… body=%s", open_id[:12], str(r2)[:160])
            return ""
        save_user_token(
            open_id,
            access_token=at,
            refresh_token=str(data.get("refresh_token") or rt),
            expires_in=int(data.get("expires_in") or 7200),
            scopes=str(data.get("scope") or ""),
        )
        return at
    except Exception as e:
        log.warning("UAT refresh error: %s", e)
        return ""


def clear_user_token(open_id: str) -> None:
    oid = (open_id or "").strip()
    if not oid:
        return
    with _LOCK:
        p = _path_for(oid)
        if p.exists():
            p.unlink()


def agent_authorize_url(*, open_id: str = "", next_path: str = "/auth/feishu/agent/done") -> str:
    """生成个人授权链接；state 带 open_id 便于回调校验。"""
    from .. import auth

    base = (os.environ.get("MESH_BASE_URL") or "http://localhost:8080").rstrip("/")
    redirect = f"{base}/auth/feishu/agent/callback"
    state = auth._state_ser.dumps(  # noqa: SLF001 — 复用签名器
        {
            "next": auth.normalize_next(next_path),
            "ts": int(time.time()),
            "kind": "agent_uat",
            "open_id": (open_id or "").strip(),
        }
    )
    q = urlencode(
        {
            "app_id": os.environ["FEISHU_APP_ID"],
            "redirect_uri": redirect,
            "scope": AGENT_USER_SCOPES,
            "state": state,
        }
    )
    return f"https://open.feishu.cn/open-apis/authen/v1/authorize?{q}"


def read_agent_state(state: str | None) -> dict[str, Any]:
    from .. import auth

    if not state:
        raise auth.FeishuLoginError("授权状态已失效，请重新打开授权链接。", "missing state")
    try:
        data = auth._state_ser.loads(state)  # noqa: SLF001
    except Exception as e:
        raise auth.FeishuLoginError("授权状态校验失败，请重新打开授权链接。", f"bad state: {e}") from e
    if time.time() - data.get("ts", 0) > auth.FEISHU_STATE_TTL:
        raise auth.FeishuLoginError("授权已超时，请重新打开授权链接。", "expired state")
    if str(data.get("kind") or "") != "agent_uat":
        raise auth.FeishuLoginError("授权类型不匹配，请使用飞书里的授权链接。", "bad kind")
    return data


def auth_guide_text(*, open_id: str, capability: str = "") -> str:
    cap = (capability or "你的个人飞书数据").strip()
    try:
        url = agent_authorize_url(open_id=open_id)
    except Exception:
        url = ""
    lines = [
        f"这件事需要你的**个人飞书授权**（读{cap}），应用身份办不了。",
        "点开下面链接，用当前飞书账号授权一次；完成后回对话再说「继续」。",
    ]
    if url:
        lines.append(url)
    else:
        lines.append("（授权链接暂不可用：检查 MESH_BASE_URL / FEISHU_APP_ID）")
    return "\n".join(lines)


def resolve_uat(*, open_id: str = "", explicit: str = "") -> str:
    ex = (explicit or "").strip()
    if ex:
        return ex
    return load_user_token(open_id)
