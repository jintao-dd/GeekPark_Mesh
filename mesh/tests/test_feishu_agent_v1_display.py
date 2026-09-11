"""Feishu Agent v1 · display_text / Evidence 对齐（纯单元，无 DB）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.feishu_reply import format_display_text
from app.agent.models import AgentAnswer, ClaimBinding
from app.agent.feishu_bot import parse_im_message, handle_feishu_event


def test_display_includes_issue_and_evidence_for_supported():
    ans = AgentAnswer(
        text="XX 尚不能证明已经量产。",
        intent="ask_published",
        evidence_refs=["ev:2026-8-17:item:4251"],
        claim_bindings=[
            ClaimBinding(
                claim="XX 尚不能证明已经量产。",
                evidence_refs=["ev:2026-8-17:item:4251"],
                status="grounded",
                reason="ok",
            )
        ],
        context={"issue_ref": {"slug": "2026-8-17"}},
        trace={},
    )
    text = format_display_text(
        ans,
        payload={
            "issue": "2026-8-17",
            "claim_support": {"support": "supported", "reason": "direct"},
        },
    )
    assert "XX 尚不能证明已经量产。" in text
    assert "期次：2026-8-17" in text or "来源" in text
    assert "可核对" in text or "4251" in text
    assert "4251" in text


def test_display_insufficient_without_related_only():
    ans = AgentAnswer(
        text="依据不足，不能确认已量产。",
        intent="ask_published",
        evidence_refs=[],
        claim_bindings=[],
        context={"issue_ref": {"slug": "2026-8-17"}},
        trace={},
    )
    text = format_display_text(
        ans,
        payload={
            "issue": "2026-8-17",
            "claim_support": {"support": "insufficient", "reason": "no_direct_support"},
            "n_hits": 2,
        },
    )
    assert "不足以" in text or "相关" in text
    assert "no_evidence" not in text.lower()
    assert "依据：暂无" not in text


def test_display_no_hit_natural():
    ans = AgentAnswer(
        text="未在已上线周报中找到与问题直接相关的记录。",
        intent="ask_published",
        evidence_refs=[],
        context={"issue_ref": {"slug": "2026-8-17"}},
        trace={},
    )
    text = format_display_text(
        ans,
        payload={"issue": "2026-8-17", "claim_support": {"support": "insufficient", "reason": "no_evidence"}, "n_hits": 0},
    )
    assert "没找到" in text or "没查" in text
    assert "no_evidence" not in text.lower()
    assert "依据：暂无直接命中" not in text


def test_refuse_no_evidence_block():
    ans = AgentAnswer(
        text="当前飞书账号尚未绑定 Mesh 用户。",
        intent="refuse",
        refused=True,
        trace={},
    )
    assert format_display_text(ans) == ans.text


def test_parse_im_message_dm():
    event = {
        "sender": {"sender_id": {"open_id": "ou_x"}},
        "message": {
            "chat_id": "oc_1",
            "chat_type": "p2p",
            "message_type": "text",
            "content": '{"text":"具身智能进展？"}',
        },
    }
    p = parse_im_message(event)
    assert p is not None
    assert p["feishu_open_id"] == "ou_x"
    assert p["channel"] == "feishu_dm"
    assert "具身智能" in p["text"]


def test_parse_im_message_mentions():
    event = {
        "sender": {"sender_id": {"open_id": "ou_sender"}},
        "message": {
            "chat_id": "oc_g",
            "chat_type": "group",
            "message_type": "text",
            "content": '{"text":"@_user_1 他是谁"}',
            "mentions": [
                {
                    "key": "@_user_1",
                    "id": {"open_id": "ou_target"},
                    "name": "张山山",
                }
            ],
        },
    }
    p = parse_im_message(event)
    assert p is not None
    assert p["channel"] == "feishu_group"
    assert p["mentions"][0]["open_id"] == "ou_target"
    assert p["mentions"][0]["name"] == "张山山"
    assert "@张山山" in p["text"]
    assert "@_user_1" not in p["text"]


def test_url_verification_challenge():
    out = handle_feishu_event(None, {"type": "url_verification", "challenge": "abc", "token": ""})
    assert out.get("challenge") == "abc"


def test_feishu_encrypt_decrypt_official_sample():
    from app.agent.feishu_bot import decrypt_feishu_encrypt

    # 飞书文档示例
    plain = decrypt_feishu_encrypt(
        "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk=",
        encrypt_key="test key",
    )
    assert plain == "hello world"


def test_encrypted_url_verification(monkeypatch):
    import base64
    import hashlib
    import json
    from Crypto.Cipher import AES
    from app.agent import feishu_bot

    monkeypatch.setenv("FEISHU_ENCRYPT_KEY", "mesh")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok123")
    payload = json.dumps(
        {"challenge": "chal-9", "token": "tok123", "type": "url_verification"},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    key = hashlib.sha256(b"mesh").digest()
    iv = b"0123456789abcdef"
    bs = 16
    pad = bs - len(payload) % bs
    payload_padded = payload + bytes([pad] * pad)
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(payload_padded)
    enc = base64.b64encode(iv + ct).decode("ascii")
    out = feishu_bot.handle_feishu_event(None, {"encrypt": enc})
    assert out.get("challenge") == "chal-9"


def test_thinking_and_answer_cards():
    from app.agent import feishu_cards

    t = feishu_cards.thinking_card(query="本期关注谁？", stage=0)
    assert t["config"]["update_multi"] is True
    assert t["header"]["title"]["content"] == "Mesh"
    assert "收到" in t["elements"][0]["text"]["content"]
    assert "你的问题" not in t["elements"][0]["text"]["content"]
    t1 = feishu_cards.thinking_card(query="本期关注谁？", stage=1)
    assert "检索" in t1["elements"][0]["text"]["content"]
    a = feishu_cards.answer_card(display_text="答案\n来源：2026-9-8 已上线周报", query="硅谷沟通了谁")
    assert a["config"]["update_multi"] is True
    assert "答案" in a["elements"][0]["text"]["content"]
    assert "**问：**" not in a["elements"][0]["text"]["content"]


def test_cardkit_v2_and_stream_prefixes():
    from app.agent import feishu_cards

    v2 = feishu_cards.thinking_card_v2(query="最近硅谷团队沟通了哪些人", stage=0)
    assert v2["schema"] == "2.0"
    assert v2["config"]["streaming_mode"] is True
    assert v2["body"]["elements"][0]["element_id"] == feishu_cards.BODY_ELEMENT_ID
    ans = feishu_cards.answer_card_v2(
        display_text="Alice 与 Bob。\n\n——\n期次：2026-9-8",
        query="最近硅谷团队沟通了哪些人",
        streaming=False,
    )
    assert ans["config"]["streaming_mode"] is False
    assert any(e.get("tag") == "action" for e in ans["body"]["elements"])
    prefs = feishu_cards.fake_stream_prefixes("第一段。第二段内容比较长用于切分。" * 3)
    assert prefs[-1].startswith(prefs[0][:10]) or len(prefs) >= 1
    assert prefs[-1].endswith("切分。") or "第一段" in prefs[-1]


def test_async_accept_spawns_and_dedups(monkeypatch):
    from app.agent import feishu_bot

    calls = []

    def fake_spawn(payload):
        calls.append(payload)

    monkeypatch.setattr(feishu_bot, "_spawn_message_job", fake_spawn)
    feishu_bot._DEDUP.clear()
    body = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_test_1",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": '{"text":"hello"}',
            },
        },
    }
    out1 = feishu_bot.handle_feishu_event(None, body)
    assert out1.get("accepted") is True
    assert out1.get("mode") == "async"
    assert len(calls) == 1
    out2 = feishu_bot.handle_feishu_event(None, body)
    assert out2.get("skipped") is True
    assert out2.get("reason") == "duplicate_event"
    assert len(calls) == 1


def test_skip_non_user_sender():
    p = parse_im_message(
        {
            "sender": {"sender_type": "app", "sender_id": {"open_id": "ou_bot"}},
            "message": {
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": '{"text":"x"}',
            },
        }
    )
    assert p is None
