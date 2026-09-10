"""⑧ 飞书 Bot 事件接线（只接线，不扩大脑）。

飞书事件 →（可选）解密 → Envelope → handle_message → display_text →（可选）回发。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any

from .harness import envelope_from_payload
from .runtime import handle_message

log = logging.getLogger("mesh.feishu_bot")


def _verification_token() -> str:
    return (os.environ.get("FEISHU_VERIFICATION_TOKEN") or "").strip()


def _encrypt_key() -> str:
    return (os.environ.get("FEISHU_ENCRYPT_KEY") or "").strip()


def decrypt_feishu_encrypt(encrypt_b64: str, encrypt_key: str | None = None) -> str:
    """解密飞书 Encrypt Key 密文（AES-256-CBC · SHA256(key) · PKCS7）。

    官方约定：密文 = base64(iv[16] + ciphertext)。
    """
    key_s = (encrypt_key if encrypt_key is not None else _encrypt_key()).strip()
    if not key_s:
        raise ValueError("FEISHU_ENCRYPT_KEY missing")
    try:
        from Crypto.Cipher import AES  # pycryptodome
    except ImportError as e:
        raise RuntimeError("pycryptodome required for Feishu encrypt decrypt") from e

    blob = base64.b64decode(encrypt_b64)
    if len(blob) < 16:
        raise ValueError("ciphertext too short")
    iv, ct = blob[:16], blob[16:]
    key = hashlib.sha256(key_s.encode("utf-8")).digest()
    plain = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
    pad = plain[-1]
    if isinstance(pad, str):  # py2 safety; unused on 3
        pad = ord(pad)
    if pad < 1 or pad > 16:
        raise ValueError("bad pkcs7 padding")
    plain = plain[:-pad]
    return plain.decode("utf-8")


def unwrap_feishu_body(body: dict[str, Any]) -> dict[str, Any]:
    """若推送为 Encrypt Key 密文包（通常仅含 encrypt），则解密为业务 JSON。"""
    body = body or {}
    enc = body.get("encrypt")
    if not enc:
        return body
    # 明文业务体不应再走 decrypt；飞书加密推送时顶层通常只有 encrypt
    if body.get("event") or body.get("header") or body.get("type") == "url_verification" or "challenge" in body:
        return body
    raw = decrypt_feishu_encrypt(str(enc))
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("decrypted feishu body is not object")
    return obj


def _extract_text(content: str) -> str:
    raw = (content or "").strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return str(obj.get("text") or obj.get("content") or "").strip()
    except Exception:
        pass
    return raw


def parse_im_message(event: dict[str, Any]) -> dict[str, Any] | None:
    """从飞书 im.message 事件抽出 harness payload。失败返回 None。"""
    msg = (event or {}).get("message") or {}
    sender = (event or {}).get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    open_id = str(sender_id.get("open_id") or "").strip()
    chat_id = str(msg.get("chat_id") or "").strip()
    chat_type = str(msg.get("chat_type") or "").strip().lower()
    msg_type = str(msg.get("message_type") or "").strip().lower()
    if msg_type and msg_type != "text":
        return None
    text = _extract_text(str(msg.get("content") or ""))
    if not text and not open_id:
        return None
    channel = "feishu_group" if chat_type == "group" else "feishu_dm"
    return {
        "text": text,
        "channel": channel,
        "feishu_open_id": open_id,
        "chat_id": chat_id,
        "thread_id": str(msg.get("thread_id") or msg.get("root_id") or "").strip(),
        "session_id": str(msg.get("chat_id") or "").strip(),
    }


def handle_feishu_event(con, body: dict[str, Any]) -> dict[str, Any]:
    """处理飞书事件回调。

    - url_verification → 回 challenge（支持 Encrypt Key 密文包）
    - im.message.receive_v1 → handle_message；回发由调用方决定（本函数返回 reply_text）
    """
    body = body or {}
    try:
        body = unwrap_feishu_body(body)
    except Exception as e:
        log.warning("feishu decrypt failed: %s", e)
        return {"ok": False, "error": "decrypt_failed", "detail": str(e)[:200]}

    # 明文 / 解密后的 challenge（URL 校验）
    if body.get("type") == "url_verification" or (
        "challenge" in body and not body.get("event") and not body.get("header")
    ):
        token = _verification_token()
        if token and body.get("token") and body.get("token") != token:
            return {"ok": False, "error": "bad_verification_token"}
        return {"challenge": body.get("challenge")}

    # 可选：校验 event token（header 或 body.token）
    token = _verification_token()
    body_token = body.get("token") or (body.get("header") or {}).get("token")
    if token and body_token and body_token != token:
        return {"ok": False, "error": "bad_verification_token"}

    header = body.get("header") or {}
    event_type = str(header.get("event_type") or body.get("type") or "").strip()
    event = body.get("event") or {}

    if event_type in ("im.message.receive_v1", "im.message.receive_v2") or (
        event.get("message") and event.get("sender")
    ):
        payload = parse_im_message(event)
        if not payload:
            return {"ok": True, "skipped": True, "reason": "unsupported_or_empty"}
        env = envelope_from_payload(payload)
        answer = handle_message(con, env)
        d = answer.to_dict()
        reply = str(d.get("display_text") or d.get("text") or "").strip()
        log.info(
            "feishu_bot reply open_id=%s intent=%s chars=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            len(reply),
        )
        return {
            "ok": True,
            "skipped": False,
            "reply_text": reply,
            "answer": d,
            "envelope": {
                "channel": payload.get("channel"),
                "feishu_open_id": payload.get("feishu_open_id"),
                "chat_id": payload.get("chat_id"),
            },
        }

    log.info("feishu_bot skipped event_type=%s keys=%s", event_type or "unknown", list(body.keys())[:8])
    return {"ok": True, "skipped": True, "reason": f"unhandled_event:{event_type or 'unknown'}"}
