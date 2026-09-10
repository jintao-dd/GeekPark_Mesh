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
    assert "期次：2026-8-17" in text
    assert "证据（对齐本答 Claim）" in text
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
        },
    )
    assert "依据判定" in text
    assert "依据不足" in text
    assert "不足以直接支持" in text


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
