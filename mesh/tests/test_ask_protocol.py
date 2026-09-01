"""Ask Analysis SSE 契约：step status。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_protocol


def test_step_event_shape():
    ev = ask_protocol.step_event(
        "cross", "timeout", analysis_id="abc", message="x", claims_n=2,
    )
    assert ev["type"] == "step"
    assert ev["step"] == "cross"
    assert ev["status"] == "timeout"
    assert ev["analysis_id"] == "abc"
    assert ev["report_id"] == "abc"
    assert ev["claims_n"] == 2


def test_source_finish_status():
    assert ask_protocol.source_finish_status({"_error": "timeout", "_fallback": True}) == "timeout"
    assert ask_protocol.source_finish_status({"_error": "boom", "facts": [], "evidence": []}) == "error"
    assert ask_protocol.source_finish_status({"facts": [{"text": "a"}]}) == "completed"


def test_cross_finish_status():
    assert ask_protocol.cross_finish_status({"_fallback": True, "_error": "timeout"}) == "timeout"
    assert ask_protocol.cross_finish_status({"summary": "ok"}) == "completed"
