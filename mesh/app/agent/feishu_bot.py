"""⑧ 飞书 Bot 事件接线（只接线，不扩大脑）。

飞书事件 →（可选）解密 → 立即回思考卡片 → 后台 Agent → Patch 最终卡片。
HTTP 回调必须在数秒内返回；重活进后台线程。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from . import feishu_api, feishu_cards
from .harness import envelope_from_payload
from .runtime import handle_message

log = logging.getLogger("mesh.feishu_bot")
# 保证 docker logs 能看到（uvicorn 下未单独配 handler 时也可能丢）
_uv = logging.getLogger("uvicorn.error")


def _elog(msg: str, *args: Any) -> None:
    try:
        log.info(msg, *args)
    except Exception:
        pass
    try:
        _uv.info("[feishu_bot] " + msg, *args)
    except Exception:
        print("[feishu_bot]", msg % args if args else msg, flush=True)

_DEDUP_LOCK = threading.Lock()
_DEDUP: dict[str, float] = {}
_DEDUP_TTL_S = 15 * 60


def _verification_token() -> str:
    return (os.environ.get("FEISHU_VERIFICATION_TOKEN") or "").strip()


def _encrypt_key() -> str:
    return (os.environ.get("FEISHU_ENCRYPT_KEY") or "").strip()


def decrypt_feishu_encrypt(encrypt_b64: str, encrypt_key: str | None = None) -> str:
    """解密飞书 Encrypt Key 密文（AES-256-CBC · SHA256(key) · PKCS7）。"""
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
    if pad < 1 or pad > 16:
        raise ValueError("bad pkcs7 padding")
    plain = plain[:-pad]
    return plain.decode("utf-8")


def unwrap_feishu_body(body: dict[str, Any]) -> dict[str, Any]:
    """若推送为 Encrypt Key 密文包，则解密为业务 JSON。"""
    body = body or {}
    enc = body.get("encrypt")
    if not enc:
        return body
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
    event = event or {}
    # 兼容少数把 message 摊平在 event 根上的形态
    msg = event.get("message") or {}
    if not msg and event.get("message_type") and (event.get("chat_id") or event.get("content") is not None):
        msg = event
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    if not sender_id and event.get("open_id"):
        sender_id = {"open_id": event.get("open_id")}
    sender_type = str(sender.get("sender_type") or event.get("sender_type") or "user").strip().lower()
    if sender_type and sender_type not in ("user",):
        return None
    open_id = str(sender_id.get("open_id") or event.get("open_id") or "").strip()
    chat_id = str(msg.get("chat_id") or event.get("chat_id") or "").strip()
    chat_type = str(msg.get("chat_type") or event.get("chat_type") or "").strip().lower()
    msg_type = str(msg.get("message_type") or event.get("message_type") or "").strip().lower()
    message_id = str(msg.get("message_id") or event.get("message_id") or "").strip()
    if msg_type and msg_type != "text":
        return None
    content = msg.get("content") if "content" in msg else event.get("content")
    if isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or "").strip()
    else:
        text = _extract_text(str(content or ""))
    if not text and not open_id:
        return None
    channel = "feishu_group" if chat_type == "group" else "feishu_dm"
    return {
        "text": text,
        "channel": channel,
        "feishu_open_id": open_id,
        "chat_id": chat_id,
        "thread_id": str(msg.get("thread_id") or msg.get("root_id") or "").strip(),
        "session_id": str(chat_id or "").strip(),
        "inbound_message_id": message_id,
    }


def _dedup_seen(message_id: str) -> bool:
    """True = 已处理过，应跳过。"""
    mid = (message_id or "").strip()
    if not mid:
        return False
    now = time.time()
    with _DEDUP_LOCK:
        # GC
        stale = [k for k, t in _DEDUP.items() if now - t > _DEDUP_TTL_S]
        for k in stale:
            _DEDUP.pop(k, None)
        if mid in _DEDUP:
            return True
        _DEDUP[mid] = now
        return False


def _receive_target(payload: dict[str, Any]) -> tuple[str, str]:
    """返回 (receive_id, receive_id_type)。优先 chat_id。"""
    chat_id = str(payload.get("chat_id") or "").strip()
    open_id = str(payload.get("feishu_open_id") or "").strip()
    if chat_id:
        return chat_id, "chat_id"
    if open_id:
        return open_id, "open_id"
    raise ValueError("no chat_id/open_id to reply")


def _schedule_thinking_nudge(
    *,
    message_id: str,
    query: str,
    done: threading.Event,
    card_lock: threading.Lock,
) -> None:
    """等待超过几秒仍未出终答时，轻推一次文案，避免「死板一张卡」。"""

    def _run() -> None:
        if done.wait(4.5):
            return
        if not message_id or done.is_set():
            return
        try:
            with card_lock:
                if done.is_set():
                    return
                feishu_api.patch_message(
                    message_id=message_id,
                    content=feishu_cards.thinking_card(query=query, stage=1),
                )
            _elog("thinking nudge patched message_id=%s", message_id)
        except Exception as e:
            log.debug("feishu thinking nudge skip: %s", e)

    threading.Thread(target=_run, name="feishu-think-nudge", daemon=True).start()


def process_feishu_message_job(payload: dict[str, Any]) -> dict[str, Any]:
    """后台任务：发思考卡 → Agent → Patch 最终卡。"""
    from .. import db

    query = str(payload.get("text") or "")
    card_message_id = ""
    done = threading.Event()
    card_lock = threading.Lock()
    d: dict[str, Any] = {}
    try:
        if feishu_api.bot_reply_enabled():
            receive_id, rid_type = _receive_target(payload)
            sent = feishu_api.send_message(
                receive_id=receive_id,
                receive_id_type=rid_type,
                msg_type="interactive",
                content=feishu_cards.thinking_card(query=query, stage=0),
            )
            card_message_id = str(sent.get("message_id") or "")
            log.info(
                "feishu thinking card sent message_id=%s chat=%s",
                card_message_id,
                payload.get("chat_id"),
            )
            _elog(
                "thinking card sent message_id=%s chat=%s",
                card_message_id,
                payload.get("chat_id"),
            )
            if card_message_id:
                _schedule_thinking_nudge(
                    message_id=card_message_id,
                    query=query,
                    done=done,
                    card_lock=card_lock,
                )
        else:
            log.warning("feishu bot reply disabled; skip outbound")
            _elog("bot reply disabled; skip outbound")

        con = db.connect()
        try:
            env = envelope_from_payload(payload)
            answer = handle_message(con, env)
            d = answer.to_dict()
            reply = str(d.get("display_text") or d.get("text") or "").strip()
        finally:
            con.close()

        _elog(
            "agent done open_id=%s intent=%s chars=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            len(reply),
        )
        log.info(
            "feishu_bot reply open_id=%s intent=%s chars=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            len(reply),
        )

        if feishu_api.bot_reply_enabled():
            final = feishu_cards.answer_card(display_text=reply, query=query)
            # 先 done，再 patch：避免 nudge 盖掉终答
            done.set()
            with card_lock:
                if card_message_id:
                    feishu_api.patch_message(message_id=card_message_id, content=final)
                else:
                    receive_id, rid_type = _receive_target(payload)
                    sent2 = feishu_api.send_message(
                        receive_id=receive_id,
                        receive_id_type=rid_type,
                        msg_type="interactive",
                        content=final,
                    )
                    card_message_id = str(sent2.get("message_id") or "")

        return {
            "ok": True,
            "reply_text": reply,
            "card_message_id": card_message_id,
            "answer": d,
        }
    except Exception as e:
        log.exception("feishu message job failed: %s", e)
        _elog("message job failed: %s", e)
        done.set()
        if feishu_api.bot_reply_enabled():
            try:
                err_card = feishu_cards.error_card(message=str(e)[:300], query=query)
                with card_lock:
                    if card_message_id:
                        feishu_api.patch_message(message_id=card_message_id, content=err_card)
                    else:
                        receive_id, rid_type = _receive_target(payload)
                        feishu_api.send_message(
                            receive_id=receive_id,
                            receive_id_type=rid_type,
                            msg_type="interactive",
                            content=err_card,
                        )
            except Exception as e2:
                log.warning("feishu error card failed: %s", e2)
        return {"ok": False, "error": str(e)[:300], "card_message_id": card_message_id}
    finally:
        done.set()


def _spawn_message_job(payload: dict[str, Any]) -> None:
    t = threading.Thread(
        target=process_feishu_message_job,
        args=(payload,),
        name="feishu-bot-msg",
        daemon=True,
    )
    t.start()


def handle_feishu_event(con, body: dict[str, Any], *, sync: bool = False) -> dict[str, Any]:
    """处理飞书事件回调。

    - url_verification → 回 challenge
    - im.message → 默认异步（立刻 accepted）；sync=True 时同步跑完（单测/联调）
    - con 在异步路径可忽略（后台自开连接）
    """
    body = body or {}
    try:
        body = unwrap_feishu_body(body)
    except Exception as e:
        log.warning("feishu decrypt failed: %s", e)
        return {"ok": False, "error": "decrypt_failed", "detail": str(e)[:200]}

    if body.get("type") == "url_verification" or (
        "challenge" in body and not body.get("event") and not body.get("header")
    ):
        token = _verification_token()
        if token and body.get("token") and body.get("token") != token:
            return {"ok": False, "error": "bad_verification_token"}
        return {"challenge": body.get("challenge")}

    token = _verification_token()
    body_token = body.get("token") or (body.get("header") or {}).get("token")
    if token and body_token and body_token != token:
        return {"ok": False, "error": "bad_verification_token"}

    header = body.get("header") or {}
    event_type = str(header.get("event_type") or body.get("type") or "").strip()
    event = body.get("event") or {}
    # 旧版：event.type == message / im.message*
    if not event_type and isinstance(event, dict):
        event_type = str(event.get("type") or event.get("event_type") or "").strip()

    _elog(
        "parsed keys=%s event_type=%s has_event=%s",
        list(body.keys())[:10],
        event_type or "unknown",
        bool(event),
    )

    if event_type in (
        "im.message.receive_v1",
        "im.message.receive_v2",
        "event_callback",
        "message",
        "im.message.receive",
    ) or (isinstance(event, dict) and (event.get("message") or event.get("message_type"))):
        payload = parse_im_message(event)
        if not payload:
            return {"ok": True, "skipped": True, "reason": "unsupported_or_empty"}
        inbound_id = str(payload.get("inbound_message_id") or "")
        if _dedup_seen(inbound_id):
            _elog("dedup skip message_id=%s", inbound_id)
            log.info("feishu dedup skip message_id=%s", inbound_id)
            return {"ok": True, "skipped": True, "reason": "duplicate_event", "inbound_message_id": inbound_id}

        if sync:
            # 单测 / 调试：同步执行（仍尽量出站）
            out = process_feishu_message_job(payload)
            out["envelope"] = {
                "channel": payload.get("channel"),
                "feishu_open_id": payload.get("feishu_open_id"),
                "chat_id": payload.get("chat_id"),
            }
            out["accepted"] = True
            out["mode"] = "sync"
            return out

        _elog(
            "accept async open_id=%s chat=%s text_len=%s",
            payload.get("feishu_open_id"),
            payload.get("chat_id"),
            len(str(payload.get("text") or "")),
        )
        _spawn_message_job(payload)
        return {
            "ok": True,
            "accepted": True,
            "mode": "async",
            "inbound_message_id": inbound_id,
            "envelope": {
                "channel": payload.get("channel"),
                "feishu_open_id": payload.get("feishu_open_id"),
                "chat_id": payload.get("chat_id"),
            },
        }

    log.info("feishu_bot skipped event_type=%s keys=%s", event_type or "unknown", list(body.keys())[:8])
    _elog("skipped event_type=%s keys=%s", event_type or "unknown", list(body.keys())[:8])
    return {"ok": True, "skipped": True, "reason": f"unhandled_event:{event_type or 'unknown'}"}
