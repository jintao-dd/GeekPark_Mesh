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
    mentions = _parse_mentions(msg.get("mentions") or event.get("mentions") or [])
    # 群聊 @机器人 会留下 @_user_1；不剥掉则确认写整句匹配失败
    if text:
        try:
            from .conversation import normalize_query

            text = normalize_query(text)
        except Exception:
            pass
    # normalize 会去掉 @；把有 open_id 的真人姓名写回正文，供同事层读懂（结构化仍在 mentions）
    if mentions:
        labels = []
        for m in mentions:
            oid = str(m.get("open_id") or "").strip()
            name = str(m.get("name") or "").strip()
            if not oid:
                continue
            labels.append(f"@{name}" if name else f"@{oid}")
        if labels:
            text = (" ".join(labels) + (" " + text if text else "")).strip()
    if not text and not open_id:
        return None
    channel = "feishu_group" if chat_type == "group" else "feishu_dm"
    return {
        "text": text,
        "channel": channel,
        "feishu_open_id": open_id,
        "chat_id": chat_id,
        # 只用话题 thread_id；切勿把 root_id（回复某条消息）当成会话隔离键，
        # 否则「准备写入」与「确认」会落到不同 session，pending_write 必丢。
        "thread_id": str(msg.get("thread_id") or "").strip(),
        "session_id": str(chat_id or "").strip(),
        "inbound_message_id": message_id,
        "mentions": mentions,
    }


def _parse_mentions(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for m in raw:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") if isinstance(m.get("id"), dict) else {}
        oid = str(
            (mid or {}).get("open_id")
            or m.get("open_id")
            or m.get("member_id")
            or ""
        ).strip()
        key = str(m.get("key") or "").strip()
        name = str(m.get("name") or "").strip()
        if not oid and not name:
            continue
        out.append({"key": key, "open_id": oid, "name": name})
    return out


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


def _schedule_stage_ticker(
    *,
    mode: str,
    query: str,
    done: threading.Event,
    card_lock: threading.Lock,
    message_id: str = "",
    card_id: str = "",
    seq: feishu_api.CardSeq | None = None,
    progress_holder: list[str] | None = None,
) -> None:
    """等待中推阶段文案；若有 Supervisor progress 则展示 Worker 进度。"""

    def _run() -> None:
        if done.wait(3.5):
            return
        if done.is_set():
            return
        prog = list(progress_holder or [])
        body = feishu_cards.stage_copy(1, query=query, progress=prog or None)
        try:
            with card_lock:
                if done.is_set():
                    return
                if mode == "cardkit" and card_id and seq is not None:
                    feishu_api.stream_card_text(
                        card_id=card_id,
                        element_id=feishu_cards.BODY_ELEMENT_ID,
                        content=body,
                        sequence=seq.next(),
                    )
                elif message_id:
                    feishu_api.patch_message(
                        message_id=message_id,
                        content=feishu_cards.thinking_card(
                            query=query, stage=1, progress=prog or None
                        ),
                    )
            _elog("stage tick stage=1 mode=%s progress=%s", mode, prog)
        except Exception as e:
            log.debug("feishu stage tick skip: %s", e)

    threading.Thread(target=_run, name="feishu-stage-tick", daemon=True).start()


def _finalize_cardkit(
    *,
    card_id: str,
    seq: feishu_api.CardSeq,
    display_text: str,
    query: str,
    card_lock: threading.Lock,
) -> None:
    """假流式：一次推全文，交给客户端打字机；很快挂上追问按钮。"""
    body = (display_text or "").strip() or "这期没捞到可引用的证据。"
    with card_lock:
        feishu_api.stream_card_text(
            card_id=card_id,
            element_id=feishu_cards.BODY_ELEMENT_ID,
            content=body,
            sequence=seq.next(),
        )
    # 只等一小段打字机，避免「答完了还卡很久才出按钮」
    time.sleep(feishu_cards.estimate_typewriter_seconds(body))
    final = feishu_cards.answer_card_v2(display_text=body, query=query, streaming=False)
    with card_lock:
        feishu_api.update_card_entity(card_id=card_id, card=final, sequence=seq.next())
        try:
            feishu_api.update_card_settings(
                card_id=card_id,
                settings={
                    "config": {
                        "streaming_mode": False,
                        "summary": {
                            "content": body.strip()[:36] or "Mesh"
                        },
                    }
                },
                sequence=seq.next(),
            )
        except Exception as e:
            log.debug("feishu close streaming skip: %s", e)


def _finalize_legacy(
    *,
    message_id: str,
    display_text: str,
    query: str,
    card_lock: threading.Lock,
    receive_id: str = "",
    rid_type: str = "chat_id",
) -> str:
    """无 CardKit：一次到位，不做分段假流式（分段会显得卡）。"""
    mid = message_id
    final = feishu_cards.answer_card(display_text=display_text, query=query)
    with card_lock:
        if mid:
            feishu_api.patch_message(message_id=mid, content=final)
        elif receive_id:
            sent = feishu_api.send_message(
                receive_id=receive_id,
                receive_id_type=rid_type,
                msg_type="interactive",
                content=final,
            )
            mid = str(sent.get("message_id") or "")
    return mid


def process_feishu_message_job(payload: dict[str, Any]) -> dict[str, Any]:
    """后台任务：阶段态思考卡 → Agent → 假流式终答（CardKit 优先）。"""
    from .. import db

    query = str(payload.get("text") or "")
    card_message_id = ""
    card_id = ""
    mode = "legacy"
    seq = feishu_api.CardSeq(1)
    done = threading.Event()
    card_lock = threading.Lock()
    progress_holder: list[str] = []
    d: dict[str, Any] = {}
    reply = ""
    try:
        if feishu_api.bot_reply_enabled():
            receive_id, rid_type = _receive_target(payload)
            if feishu_api.cardkit_enabled():
                try:
                    entity = feishu_cards.thinking_card_v2(query=query, stage=0)
                    card_id = feishu_api.create_card_entity(entity)
                    sent = feishu_api.send_card_entity(
                        receive_id=receive_id,
                        receive_id_type=rid_type,
                        card_id=card_id,
                    )
                    card_message_id = str(sent.get("message_id") or "")
                    mode = "cardkit"
                except Exception as e:
                    log.warning("feishu cardkit unavailable, fallback legacy: %s", e)
                    _elog("cardkit fallback: %s", e)
                    card_id = ""
            if mode != "cardkit":
                sent = feishu_api.send_message(
                    receive_id=receive_id,
                    receive_id_type=rid_type,
                    msg_type="interactive",
                    content=feishu_cards.thinking_card(query=query, stage=0),
                )
                card_message_id = str(sent.get("message_id") or "")
                mode = "legacy"

            log.info(
                "feishu thinking sent mode=%s message_id=%s card_id=%s",
                mode,
                card_message_id,
                card_id,
            )
            _elog(
                "thinking sent mode=%s message_id=%s card_id=%s",
                mode,
                card_message_id,
                card_id,
            )
            _schedule_stage_ticker(
                mode=mode,
                query=query,
                done=done,
                card_lock=card_lock,
                message_id=card_message_id,
                card_id=card_id,
                seq=seq if mode == "cardkit" else None,
                progress_holder=progress_holder,
            )
        else:
            log.warning("feishu bot reply disabled; skip outbound")
            _elog("bot reply disabled; skip outbound")

        def _on_progress(label: str) -> None:
            s = str(label or "").strip()
            if not s or s in progress_holder:
                return
            progress_holder.append(s)
            if done.is_set() or not feishu_api.bot_reply_enabled():
                return
            body = feishu_cards.stage_copy(1, query=query, progress=progress_holder)
            try:
                with card_lock:
                    if done.is_set():
                        return
                    if mode == "cardkit" and card_id:
                        feishu_api.stream_card_text(
                            card_id=card_id,
                            element_id=feishu_cards.BODY_ELEMENT_ID,
                            content=body,
                            sequence=seq.next(),
                        )
                    elif card_message_id:
                        feishu_api.patch_message(
                            message_id=card_message_id,
                            content=feishu_cards.thinking_card(
                                query=query, stage=1, progress=progress_holder
                            ),
                        )
            except Exception as e:
                log.debug("feishu progress patch skip: %s", e)

        from .supervisor import progress as progressmod

        prog_token = progressmod.set_progress_callback(_on_progress)
        con = db.connect()
        try:
            env = envelope_from_payload(payload)
            answer = handle_message(con, env)
            d = answer.to_dict()
            reply = str(d.get("display_text") or d.get("text") or "").strip()
        finally:
            con.close()
            progressmod.reset_progress_callback(prog_token)

        tr = d.get("trace") if isinstance(d.get("trace"), dict) else {}
        dec = tr.get("decision") if isinstance(tr.get("decision"), dict) else {}
        _elog(
            "agent done open_id=%s intent=%s action=%s pending=%s q=%r chars=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            dec.get("action") or "",
            tr.get("pending_write") or "",
            str(payload.get("text") or "")[:60],
            len(reply),
        )
        log.info(
            "feishu_bot reply open_id=%s intent=%s chars=%s mode=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            len(reply),
            mode,
        )

        if feishu_api.bot_reply_enabled():
            done.set()
            if mode == "cardkit" and card_id:
                _finalize_cardkit(
                    card_id=card_id,
                    seq=seq,
                    display_text=reply,
                    query=query,
                    card_lock=card_lock,
                )
            else:
                receive_id, rid_type = _receive_target(payload)
                card_message_id = _finalize_legacy(
                    message_id=card_message_id,
                    display_text=reply,
                    query=query,
                    card_lock=card_lock,
                    receive_id=receive_id,
                    rid_type=rid_type,
                )

        return {
            "ok": True,
            "reply_text": reply,
            "card_message_id": card_message_id,
            "card_id": card_id,
            "mode": mode,
            "answer": d,
        }
    except Exception as e:
        log.exception("feishu message job failed: %s", e)
        _elog("message job failed: %s", e)
        done.set()
        if feishu_api.bot_reply_enabled():
            try:
                with card_lock:
                    if mode == "cardkit" and card_id:
                        err = feishu_cards.error_card_v2(message=str(e)[:300], query=query)
                        feishu_api.update_card_entity(
                            card_id=card_id, card=err, sequence=seq.next()
                        )
                        try:
                            feishu_api.update_card_settings(
                                card_id=card_id,
                                settings={"config": {"streaming_mode": False}},
                                sequence=seq.next(),
                            )
                        except Exception:
                            pass
                    else:
                        err_card = feishu_cards.error_card(message=str(e)[:300], query=query)
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
        return {
            "ok": False,
            "error": str(e)[:300],
            "card_message_id": card_message_id,
            "card_id": card_id,
            "mode": mode,
        }
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


def _parse_card_action(event: dict[str, Any]) -> dict[str, Any] | None:
    """卡片按钮回传 → 伪造一条用户提问 payload。"""
    action = event.get("action") or {}
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {"q": value}
    if not isinstance(value, dict):
        value = {}
    q = str(value.get("q") or value.get("text") or "").strip()
    if not q:
        return None
    open_id = ""
    operator = event.get("operator") or {}
    if isinstance(operator, dict):
        open_id = str(operator.get("open_id") or "").strip()
    sender = event.get("sender") or {}
    if not open_id and isinstance(sender, dict):
        sid = sender.get("sender_id") or sender.get("id") or {}
        if isinstance(sid, dict):
            open_id = str(sid.get("open_id") or "").strip()
    ctx = event.get("context") or {}
    chat_id = ""
    if isinstance(ctx, dict):
        chat_id = str(ctx.get("open_chat_id") or ctx.get("chat_id") or "").strip()
    if not chat_id:
        chat_id = str(event.get("open_chat_id") or event.get("chat_id") or "").strip()
    return {
        "text": q,
        "feishu_open_id": open_id,
        "chat_id": chat_id,
        "chat_type": "p2p",
        "inbound_message_id": f"card_action:{q[:40]}:{int(time.time())}",
        "channel": "feishu",
    }


def handle_feishu_event(con, body: dict[str, Any], *, sync: bool = False) -> dict[str, Any]:
    """处理飞书事件回调。

    - url_verification → 回 challenge
    - im.message → 默认异步（立刻 accepted）；sync=True 时同步跑完（单测/联调）
    - card.action.trigger → 追问按钮
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

    if event_type in ("card.action.trigger", "card.action.trigger_v1"):
        payload = _parse_card_action(event if isinstance(event, dict) else {})
        if not payload:
            return {"ok": True, "skipped": True, "reason": "empty_card_action"}
        _elog("card action ask q=%s", payload.get("text"))
        if sync:
            out = process_feishu_message_job(payload)
            out["accepted"] = True
            out["mode"] = "sync"
            return out
        _spawn_message_job(payload)
        return {"ok": True, "accepted": True, "mode": "async", "source": "card_action"}

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
