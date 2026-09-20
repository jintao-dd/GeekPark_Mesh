"""⑧ 飞书 Bot 事件接线（只接线，不扩大脑）。

飞书事件 →（可选）解密 → 立即回思考卡片 → 后台 Agent → Patch 最终卡片。
HTTP 回调必须在数秒内返回；重活进后台线程。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Any

from . import feishu_api, feishu_cards
from .harness import envelope_from_payload
from .runtime import handle_message

# 飞书消息 job 线程池：避免突发流量下无限制创建 OS 线程
_FEISHU_JOB_POOL: ThreadPoolExecutor | None = None
_FEISHU_JOB_POOL_LOCK = threading.Lock()


def _get_feishu_job_pool() -> ThreadPoolExecutor:
    global _FEISHU_JOB_POOL
    if _FEISHU_JOB_POOL is None:
        with _FEISHU_JOB_POOL_LOCK:
            if _FEISHU_JOB_POOL is None:
                max_workers = int(os.environ.get("MESH_FEISHU_JOB_WORKERS") or 32)
                _FEISHU_JOB_POOL = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="feishu-bot-msg",
                )
    return _FEISHU_JOB_POOL

log = logging.getLogger("mesh.feishu_bot")


def _true_stream_enabled() -> bool:
    """飞书答案上屏开关（FEISHU_TRUE_STREAM）。

    开启后用「缓冲混合流」：先攒约 120 字再开播，之后按大段续推；
    兼顾首字速度与匀速观感。默认关。
    """
    flag = (os.environ.get("FEISHU_TRUE_STREAM") or "0").strip().lower()
    return flag in ("1", "true", "yes", "on")


# 混合流：开播门槛 / 续推门槛（字）与最小推送间隔（秒）
# 开播门槛不宜过高：短答（<120）从未推送时，若收尾又误判「已同步」会永久停在思考文案。
_STREAM_OPEN_CHARS = 36
_STREAM_STEP_CHARS = 60
_STREAM_MIN_INTERVAL_S = 0.15

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
    """若推送为 Encrypt Key 密文包，则解密为业务 JSON。

    安全：只要带 `encrypt` 就**必须**解密。历史实现会在包里同时出现
    `event`/`header`/`challenge` 时直接当明文放行，攻击者只要在密文包里
    额外塞一个 `event` 键即可绕过解密与后续校验，故这里一律解密。
    """
    body = body or {}
    enc = body.get("encrypt")
    if not enc:
        return body
    raw = decrypt_feishu_encrypt(str(enc))
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("decrypted feishu body is not object")
    return obj


def verify_feishu_signature(
    headers: Any,
    raw_body: bytes,
) -> tuple[bool, str]:
    """校验飞书回调签名（X-Lark-Signature）。

    算法：sha256(timestamp + nonce + encrypt_key + body) 十六进制。
    - 未配置 Encrypt Key：无从校验，返回 (True, "no_key")；
    - 带签名头：必须匹配，且时间戳须在 ±FEISHU_SIGNATURE_TTL_S 内（防重放）；
    - 无签名头：FEISHU_SIGNATURE_REQUIRED=1 时拒绝，否则放行（记警告）。
    """
    key_s = _encrypt_key()
    if not key_s:
        return True, "no_key"
    get = getattr(headers, "get", None)
    if not callable(get):
        return True, "no_headers"
    sig = str(get("X-Lark-Signature") or get("x-lark-signature") or "").strip()
    ts = str(get("X-Lark-Request-Timestamp") or "").strip()
    nonce = str(get("X-Lark-Request-Nonce") or "").strip()
    if not sig:
        required = (os.environ.get("FEISHU_SIGNATURE_REQUIRED") or "0").strip().lower()
        if required in ("1", "true", "yes", "on"):
            return False, "missing_signature"
        log.warning("feishu signature header absent; set FEISHU_SIGNATURE_REQUIRED=1 to enforce")
        return True, "no_signature"
    if not ts or not nonce:
        return False, "missing_timestamp_or_nonce"
    try:
        age = abs(time.time() - float(ts))
    except Exception:
        return False, "bad_timestamp"
    ttl = float(os.environ.get("FEISHU_SIGNATURE_TTL_S") or 300)
    if age > ttl:
        return False, "stale_timestamp"
    payload = (ts + nonce + key_s).encode("utf-8") + (raw_body or b"")
    expect = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return False, "bad_signature"
    if _dedup_seen(f"sig:{nonce}:{int(float(ts))}"):
        return False, "replayed_nonce"
    return True, "ok"


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
    answer_started: threading.Event | None = None,
) -> None:
    """等待中动态轮播阶段文案；优先展示 Supervisor progress，无则按步骤递增。

    UX：
    - spinner ~200ms/帧（够活，又不至于打爆飞书 API / 抢答案推送锁）；
    - 阶段文案约 1.6s 换一句；
    - 约 8s 后降到 ~400ms/帧；
    - body 未变则跳过飞书调用。
    """

    def _run() -> None:
        stage_index = 0
        tick = 0
        last_body: str | None = None
        fast_interval = 0.20
        slow_interval = 0.40
        fast_cutoff_ticks = 40  # ~8s
        stage_step_ticks_fast = 8   # ~1.6s 换一句
        stage_step_ticks_slow = 5   # ~2.0s 换一句
        while not done.wait(fast_interval if tick < fast_cutoff_ticks else slow_interval):
            if done.is_set():
                return
            # 答案已开始真流式：让出 mesh_body，别再写进度覆盖答案
            if answer_started is not None and answer_started.is_set():
                return
            prog = list(progress_holder or [])
            body = feishu_cards.stage_copy(
                query=query,
                progress=prog or None,
                stage_index=stage_index,
                tick=tick,
            )
            if body == last_body:
                # body 未变，跳过本次 API 调用，但 stage_index 仍要推进
                tick += 1
                step_ticks = stage_step_ticks_fast if tick < fast_cutoff_ticks else stage_step_ticks_slow
                if tick % step_ticks == 0:
                    stage_index += 1
                continue
            last_body = body
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
                                query=query,
                                stage=stage_index,
                                progress=prog or None,
                            ),
                        )
                _elog(
                    "stage tick stage=%s tick=%s mode=%s progress=%s",
                    stage_index,
                    tick,
                    mode,
                    prog,
                )
            except Exception as e:
                log.debug("feishu stage tick skip: %s", e)
            tick += 1
            step_ticks = stage_step_ticks_fast if tick < fast_cutoff_ticks else stage_step_ticks_slow
            if tick % step_ticks == 0:
                stage_index += 1

    threading.Thread(target=_run, name="feishu-stage-tick", daemon=True).start()


def _finalize_cardkit(
    *,
    card_id: str,
    seq: feishu_api.CardSeq,
    display_text: str,
    query: str,
    card_lock: threading.Lock,
    message_id: str = "",
    payload: dict[str, Any] | None = None,
    streamed: bool = False,
    streamed_text: str = "",
    pushed_text: str = "",
) -> None:
    """完成回答。

    streamed=True（FEISHU_TRUE_STREAM）：混合流收尾——以「飞书上实际推过的正文」为准补齐终稿，
    再关 streaming；不整卡换模板、不发新卡。

    关键：必须用 pushed_text（真推过的），不能用 streamed_text/final_acc（内存全文）。
    否则会出现：短答从未开播 → 卡死在思考文案；或末段未推 → 用户看到截断。
    """
    body = (display_text or "").strip() or "这期没捞到可引用的证据。"

    if streamed:
        on_card = (pushed_text or "").strip()
        need_push = on_card != body
        try:
            if need_push:
                with card_lock:
                    feishu_api.stream_card_text(
                        card_id=card_id,
                        element_id=feishu_cards.BODY_ELEMENT_ID,
                        content=body,
                        sequence=seq.next(),
                    )
                # 给客户端一点时间把终稿画完再关 streaming，避免「写到一半被掐断」
                time.sleep(min(0.55, max(0.2, feishu_cards.estimate_typewriter_seconds(body))))
        except feishu_api.StreamingModeClosedError:
            pass
        except Exception as e:
            log.warning("feishu hybrid-stream final push skip: %s", e)
        try:
            with card_lock:
                feishu_api.update_card_settings(
                    card_id=card_id,
                    settings={"config": {"streaming_mode": False,
                                          "summary": {"content": body.strip()[:36] or "Mesh"}}},
                    sequence=seq.next(),
                )
        except Exception as e:
            log.warning("feishu stream close streaming skip: %s", e)
        return

    # ===== 非流式（原逻辑）：发一张全新答案卡触发通知 =====
    # 1) 先关闭 streaming（避免后续操作命中 300309）
    with card_lock:
        try:
            feishu_api.update_card_settings(
                card_id=card_id,
                settings={
                    "config": {
                        "streaming_mode": False,
                        "summary": {"content": body.strip()[:36] or "Mesh"},
                    }
                },
                sequence=seq.next(),
            )
        except Exception as e:
            log.warning("feishu close streaming skip: %s", e)

    # 2) 发送一张全新的答案卡片（作为新消息，会触发飞书通知）
    # 必须使用新的 card_id：飞书对同一 card_id 的绑定次数有限制，复用旧 ID 会触发
    # 200780 "card binding biz count over limit"，导致答案发不出去。
    final = feishu_cards.answer_card_v2(display_text=body, query=query, streaming=False)
    try:
        receive_id, rid_type = _receive_target(payload or {})
        answer_card_id = feishu_api.create_card_entity(final)
        sent = feishu_api.send_card_entity(
            receive_id=receive_id,
            receive_id_type=rid_type,
            card_id=answer_card_id,
        )
        _elog(
            "answer card sent message_id=%s answer_card_id=%s old_card_id=%s",
            sent.get("message_id"),
            answer_card_id,
            card_id,
        )
    except Exception as e:
        log.warning("feishu send answer card failed: %s", e)
        # fallback：如果发新消息失败，至少把原卡片更新为答案
        with card_lock:
            feishu_api.update_card_entity(
                card_id=card_id, card=final, sequence=seq.next()
            )


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
    # 阶段耗时埋点：毫秒级，统一在 finalize 时输出
    timings: dict[str, int] = {}
    t0 = time.time()
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

            timings["thinking_ms"] = int((time.time() - t0) * 1000)
            log.info(
                "feishu thinking sent mode=%s message_id=%s card_id=%s thinking_ms=%s",
                mode,
                card_message_id,
                card_id,
                timings["thinking_ms"],
            )
            _elog(
                "thinking sent mode=%s message_id=%s card_id=%s thinking_ms=%s",
                mode,
                card_message_id,
                card_id,
                timings["thinking_ms"],
            )
            # 真流式：答案首个 chunk 到达后置位，通知进度 ticker/刷新让出 mesh_body
            answer_started = threading.Event()
            _schedule_stage_ticker(
                mode=mode,
                query=query,
                done=done,
                card_lock=card_lock,
                message_id=card_message_id,
                card_id=card_id,
                seq=seq if mode == "cardkit" else None,
                progress_holder=progress_holder,
                answer_started=answer_started,
            )
        else:
            log.warning("feishu bot reply disabled; skip outbound")
            _elog("bot reply disabled; skip outbound")
            answer_started = threading.Event()

        # progress 节流：合并高频 label，最多 1 秒刷新一次卡片
        _progress_state = {
            "lock": threading.Lock(),
            "pending_labels": [],
            "last_body": None,
            "last_at": 0.0,
        }

        def _flush_progress_card() -> None:
            st = _progress_state
            with st["lock"]:
                labels = list(st["pending_labels"])
                st["pending_labels"].clear()
            if not labels or done.is_set() or not feishu_api.bot_reply_enabled():
                return
            # 答案已开始真流式：不再写进度，避免覆盖答案
            if answer_started.is_set():
                return
            # 避免重复 label 追加
            for s in labels:
                if s and s not in progress_holder:
                    progress_holder.append(s)
            body = feishu_cards.stage_copy(
                query=query,
                progress=progress_holder,
                stage_index=len(progress_holder),
                tick=len(progress_holder),
            )
            if body == st["last_body"]:
                return
            st["last_body"] = body
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
                                query=query,
                                stage=len(progress_holder),
                                progress=progress_holder,
                            ),
                        )
                    st["last_at"] = time.time()
            except Exception as e:
                log.debug("feishu progress patch skip: %s", e)

        def _on_progress(label: str) -> None:
            s = str(label or "").strip()
            if not s or s in progress_holder:
                return
            st = _progress_state
            with st["lock"]:
                st["pending_labels"].append(s)
            # 如果距离上次刷新已超过 1 秒，立即刷新；否则由后台 ticker 兜底
            if time.time() - st["last_at"] >= 1.0:
                _flush_progress_card()

        from .supervisor import progress as progressmod

        prog_token = progressmod.set_progress_callback(_on_progress)

        # 混合流上屏：先攒少量字再开播，之后大段续推。
        # pushed_text = 飞书卡片上实际推过的正文（收尾必须以它为准，防卡住/截断）。
        stream_state = {
            "lock": threading.Lock(),
            "last_push_at": 0.0,
            "last_len": 0,
            "streamed_any": False,
            "final_acc": "",
            "pushed_text": "",
            "opened": False,
            "_answer_cfg": False,
        }
        true_stream_on = _true_stream_enabled() and mode == "cardkit" and bool(card_id)

        def _on_answer_delta(delta: str, accumulated: str) -> None:
            if not true_stream_on or done.is_set():
                return
            if not answer_started.is_set():
                answer_started.set()
            st = stream_state
            now = time.time()
            to_push: str | None = None
            with st["lock"]:
                st["final_acc"] = accumulated
                st["streamed_any"] = True
                n = len(accumulated)
                if not st["opened"]:
                    if n >= _STREAM_OPEN_CHARS:
                        st["opened"] = True
                        st["last_push_at"] = now
                        st["last_len"] = n
                        to_push = accumulated
                else:
                    grew = n - st["last_len"]
                    waited = now - st["last_push_at"]
                    if grew >= _STREAM_STEP_CHARS or (waited >= _STREAM_MIN_INTERVAL_S and grew >= 20):
                        st["last_push_at"] = now
                        st["last_len"] = n
                        to_push = accumulated
            if not to_push:
                return
            try:
                with card_lock:
                    if done.is_set():
                        return
                    # 答案首次上屏：切到答案打字机配置（思考卡是瞬上配置）
                    if not st.get("_answer_cfg"):
                        st["_answer_cfg"] = True
                        try:
                            feishu_api.update_card_settings(
                                card_id=card_id,
                                settings={"config": {
                                    "streaming_mode": True,
                                    "streaming_config": feishu_cards.streaming_config(),
                                }},
                                sequence=seq.next(),
                            )
                        except Exception as e:
                            log.debug("feishu answer streaming_config skip: %s", e)
                    feishu_api.stream_card_text(
                        card_id=card_id,
                        element_id=feishu_cards.BODY_ELEMENT_ID,
                        content=to_push,
                        sequence=seq.next(),
                    )
                with st["lock"]:
                    st["pushed_text"] = to_push
            except feishu_api.StreamingModeClosedError:
                pass
            except Exception as e:
                log.debug("feishu hybrid-stream delta push skip: %s", e)

        astream_mod = None
        astream_token = ""
        if true_stream_on:
            try:
                from . import answer_stream as astream_mod
                # 每个 job 一个 key：并发下回调不互相覆盖/误清（防答案写进别人的卡片）
                job_stream_key = f"{payload.get('inbound_message_id') or ''}|{id(stream_state)}"
                astream_mod.set_current_key(job_stream_key)
                astream_token = astream_mod.set_delta_callback(
                    _on_answer_delta, key=job_stream_key
                )
            except Exception as e:
                log.warning("feishu true-stream setup failed: %s", e)
                astream_mod = None

        t_agent = time.time()
        con = db.connect()
        try:
            env = envelope_from_payload(payload)
            answer = handle_message(con, env)
            d = answer.to_dict()
            reply = str(d.get("display_text") or d.get("text") or "").strip()
        finally:
            con.close()
            progressmod.reset_progress_callback(prog_token)
            if astream_mod is not None:
                astream_mod.reset_delta_callback(astream_token)
        timings["agent_ms"] = int((time.time() - t_agent) * 1000)

        tr = d.get("trace") if isinstance(d.get("trace"), dict) else {}
        dec = tr.get("decision") if isinstance(tr.get("decision"), dict) else {}
        # 提取已有内部耗时（controller / supervisor 等）
        inner_timing: dict[str, Any] = {}
        if isinstance(tr.get("controller"), dict):
            inner_timing["controller_ms"] = tr["controller"].get("controller_latency_ms")
        if isinstance(tr.get("timings"), dict):
            inner_timing.update(tr["timings"])
        _elog(
            "agent done open_id=%s intent=%s action=%s pending=%s q=%r chars=%s timings=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            dec.get("action") or "",
            tr.get("pending_write") or "",
            str(payload.get("text") or "")[:60],
            len(reply),
            inner_timing,
        )
        log.info(
            "feishu_bot reply open_id=%s intent=%s chars=%s mode=%s",
            payload.get("feishu_open_id"),
            d.get("intent"),
            len(reply),
            mode,
        )

        started_at: float = payload.get("_mesh_started_at") or 0.0
        elapsed_ms = int((time.time() - started_at) * 1000) if started_at else 0
        timings["total_ms"] = elapsed_ms
        _elog(
            "finalize timing started_at=%s elapsed_ms=%s timings=%s",
            started_at,
            elapsed_ms,
            timings,
        )

        t_finalize = time.time()
        if feishu_api.bot_reply_enabled():
            done.set()
            if mode == "cardkit" and card_id:
                _finalize_cardkit(
                    card_id=card_id,
                    seq=seq,
                    display_text=reply,
                    query=query,
                    card_lock=card_lock,
                    message_id=card_message_id,
                    payload=payload,
                    streamed=bool(stream_state.get("streamed_any")),
                    streamed_text=str(stream_state.get("final_acc") or ""),
                    pushed_text=str(stream_state.get("pushed_text") or ""),
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
        timings["finalize_ms"] = int((time.time() - t_finalize) * 1000)
        timings["job_ms"] = int((time.time() - t0) * 1000)
        _elog(
            "feishu message job finished total_ms=%s timings=%s",
            timings["job_ms"],
            timings,
        )

        return {
            "ok": True,
            "reply_text": reply,
            "card_message_id": card_message_id,
            "card_id": card_id,
            "mode": mode,
            "answer": d,
            "timings": timings,
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
    _get_feishu_job_pool().submit(process_feishu_message_job, payload)


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
    # 卡片事件自带的 message_id：用它去重，避免连点重复执行
    mid = ""
    if isinstance(ctx, dict):
        mid = str(ctx.get("open_message_id") or ctx.get("message_id") or "").strip()
    if not mid:
        mid = str(event.get("open_message_id") or event.get("message_id") or "").strip()
    # 会话键须与正常飞书消息一致：群 chat_id 前缀 oc_，私聊为 ou_/open_id。
    # 历史实现固定 channel="feishu"，导致 session_key_of 落到 anon:* 另一个会话，
    # 按钮追问会丢失上一轮上下文。
    is_group = chat_id.startswith("oc_")
    return {
        "text": q,
        "feishu_open_id": open_id,
        "chat_id": chat_id,
        "chat_type": "group" if is_group else "p2p",
        "inbound_message_id": mid or f"card_action:{q[:40]}:{int(time.time() * 1000)}",
        "channel": "feishu_group" if is_group else "feishu_dm",
    }


def handle_feishu_event(
    con,
    body: dict[str, Any],
    *,
    sync: bool = False,
    headers: Any = None,
    raw_body: bytes | None = None,
) -> dict[str, Any]:
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

    # 签名校验（防伪造 / 重放）；Encrypt Key 未配置时无从校验
    if headers is not None or raw_body is not None:
        ok_sig, sig_reason = verify_feishu_signature(headers, raw_body or b"")
        if not ok_sig:
            _elog("feishu signature rejected reason=%s", sig_reason)
            return {"ok": False, "error": "bad_signature", "reason": sig_reason}

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

        payload["_mesh_started_at"] = time.time()

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
