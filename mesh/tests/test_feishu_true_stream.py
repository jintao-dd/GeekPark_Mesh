"""方案 B 端到端真流式：单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_answer_stream_callback_bus():
    from app.agent import answer_stream

    got = []
    answer_stream.set_delta_callback(lambda d, acc: got.append((d, acc)))
    # 注册后默认不推（防中间稿抢写）；需 begin_final 才 active
    assert answer_stream.stream_active() is False
    answer_stream.begin_final_stream()
    assert answer_stream.stream_active() is True
    answer_stream.emit_delta("你", "你")
    answer_stream.emit_delta("好", "你好")
    assert got == [("你", "你"), ("好", "你好")]
    answer_stream.end_final_stream()
    assert answer_stream.stream_active() is False
    answer_stream.reset_delta_callback()
    assert answer_stream.stream_active() is False
    # reset 后 emit 不再触发
    answer_stream.emit_delta("x", "x")
    assert len(got) == 2


def test_answer_stream_swallows_callback_errors():
    from app.agent import answer_stream

    def boom(d, acc):
        raise RuntimeError("should not propagate")

    answer_stream.set_delta_callback(boom)
    answer_stream.begin_final_stream()
    # 不抛
    answer_stream.emit_delta("a", "a")
    answer_stream.reset_delta_callback()


def test_answer_stream_gate_blocks_intermediate():
    """未 begin_final 时，即使回调已注册也不推、不走流式。"""
    from app.agent import answer_stream

    got = []
    answer_stream.set_delta_callback(lambda d, acc: got.append(d))
    assert answer_stream.stream_active() is False
    answer_stream.emit_delta("中间稿", "中间稿")
    assert got == []
    answer_stream.begin_final_stream()
    answer_stream.emit_delta("终答", "终答")
    assert got == ["终答"]
    answer_stream.reset_delta_callback()


def test_llm_call_uses_stream_when_active(monkeypatch):
    from app import llm
    from app.agent import answer_stream

    class FakeProvider:
        def stream(self, system, user, max_tokens=4000, *, task="default"):
            for ch in ["Hel", "lo", "!"]:
                yield ch

        def complete_detail(self, system, user, max_tokens=4000, *, task="default"):
            return {"content": "SHOULD_NOT_BE_USED", "usage": {}}

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(llm, "model_for_task", lambda task="default": "fake-model")

    deltas = []
    answer_stream.set_delta_callback(lambda d, acc: deltas.append(d))
    answer_stream.begin_final_stream()
    try:
        out = llm.call("sys", "usr", json_mode=False, task="answer")
    finally:
        answer_stream.reset_delta_callback()

    assert out == "Hello!"
    assert deltas == ["Hel", "lo", "!"]


def test_llm_call_intermediate_answer_not_streamed(monkeypatch):
    """回调已注册但未 begin_final → 中间 ask/answer 走非流式，不推飞书。"""
    from app import llm
    from app.agent import answer_stream

    class FakeProvider:
        def stream(self, system, user, max_tokens=4000, *, task="default"):
            yield "STREAMED"

        def complete_detail(self, system, user, max_tokens=4000, *, task="default"):
            return {"content": "NONSTREAM", "usage": {}}

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(llm, "model_for_task", lambda task="default": "fake-model")

    called = []
    answer_stream.set_delta_callback(lambda d, acc: called.append(d))
    # 故意不 begin_final
    try:
        out = llm.call("sys", "usr", json_mode=False, task="answer")
    finally:
        answer_stream.reset_delta_callback()
    assert out == "NONSTREAM"
    assert called == []


def test_llm_call_non_answer_task_not_streamed(monkeypatch):
    from app import llm
    from app.agent import answer_stream

    class FakeProvider:
        def stream(self, system, user, max_tokens=4000, *, task="default"):
            yield "STREAMED"

        def complete_detail(self, system, user, max_tokens=4000, *, task="default"):
            return {"content": "NONSTREAM", "usage": {}}

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(llm, "model_for_task", lambda task="default": "fake-model")

    called = []
    answer_stream.set_delta_callback(lambda d, acc: called.append(d))
    answer_stream.begin_final_stream()
    try:
        # task=default 不应走流式
        out = llm.call("sys", "usr", json_mode=False, task="default")
    finally:
        answer_stream.reset_delta_callback()
    assert out == "NONSTREAM"
    assert called == []


def test_finalize_cardkit_streamed_does_not_send_new_card(monkeypatch):
    from app.agent import feishu_api, feishu_bot

    events = []
    monkeypatch.setattr(feishu_api, "update_card_settings", lambda **kw: events.append(("settings", kw)))
    monkeypatch.setattr(feishu_api, "update_card_entity", lambda **kw: events.append(("entity", kw)))
    monkeypatch.setattr(feishu_api, "stream_card_text", lambda **kw: events.append(("stream", kw)))

    def _should_not_call(**kw):
        raise AssertionError("streamed finalize must NOT create/send a new card")

    monkeypatch.setattr(feishu_api, "create_card_entity", _should_not_call)
    monkeypatch.setattr(feishu_api, "send_card_entity", _should_not_call)

    import threading
    feishu_bot._finalize_cardkit(
        card_id="c1",
        seq=feishu_api.CardSeq(1),
        display_text="最终清洗后的答案。",
        query="随便问",
        card_lock=threading.Lock(),
        streamed=True,
        streamed_text="最终清洗后的答案。",
    )
    kinds = [e[0] for e in events]
    # 终稿==已推 → 不额外 stream 覆盖；只关 streaming 定格，不整卡换模板（避免闪动）
    assert "settings" in kinds
    assert "entity" not in kinds, "streamed finalize must NOT整卡换模板（会闪）"


def test_finalize_cardkit_streamed_overwrites_when_final_differs(monkeypatch):
    from app.agent import feishu_api, feishu_bot

    events = []
    monkeypatch.setattr(feishu_api, "update_card_settings", lambda **kw: events.append(("settings", kw)))
    monkeypatch.setattr(feishu_api, "update_card_entity", lambda **kw: events.append(("entity", kw)))
    monkeypatch.setattr(feishu_api, "stream_card_text", lambda **kw: events.append(("stream", kw)))
    monkeypatch.setattr(feishu_api, "create_card_entity", lambda **kw: (_ for _ in ()).throw(AssertionError("no new card")))
    monkeypatch.setattr(feishu_api, "send_card_entity", lambda **kw: (_ for _ in ()).throw(AssertionError("no new card")))

    import threading
    feishu_bot._finalize_cardkit(
        card_id="c1",
        seq=feishu_api.CardSeq(1),
        display_text="依据不足，暂不能确认。",   # 终稿
        query="X 量产了吗",
        card_lock=threading.Lock(),
        streamed=True,
        streamed_text="X 已经量产了。",       # 毛坯（不同）→ 必须覆盖
    )
    # 终稿 != 已推 → 必须有一次 stream 覆盖成终稿
    stream_events = [e for e in events if e[0] == "stream"]
    assert stream_events, "final differs from streamed → must overwrite via stream_card_text"
    assert stream_events[0][1]["content"] == "依据不足，暂不能确认。"


def test_true_stream_flag(monkeypatch):
    from app.agent import feishu_bot

    monkeypatch.setenv("FEISHU_TRUE_STREAM", "1")
    assert feishu_bot._true_stream_enabled() is True
    monkeypatch.setenv("FEISHU_TRUE_STREAM", "0")
    assert feishu_bot._true_stream_enabled() is False
    monkeypatch.delenv("FEISHU_TRUE_STREAM", raising=False)
    assert feishu_bot._true_stream_enabled() is False
