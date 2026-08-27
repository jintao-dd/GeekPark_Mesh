import json
from pathlib import Path

import pytest

from app.edm import (
    _fallback_subject_highlight,
    _subject_date,
    _subject_emoji,
    _subject_valid_highlight,
    build_edm_subject,
)


def _load_issue_data():
    p = Path(__file__).resolve().parents[1] / "deploy" / "_issue_2026-8-17.json"
    if not p.exists():
        pytest.skip("fixture missing")
    payload = json.loads(p.read_text(encoding="utf-8"))
    return payload["issue"], payload["data"]


def test_subject_date_from_date_end():
    assert _subject_date({"date_end": "2026-08-27"}) == "08.27"


def test_subject_emoji_deterministic():
    assert _subject_emoji("2026-8-17") == _subject_emoji("2026-8-17")
    assert _subject_emoji("2026-8-17") in "🐊🐉🐢🦕🦗🌻🌵🌴🧃🍵🏜️🗽🚛🎋🔫🔋"


def test_subject_format_real_issue():
    issue, data = _load_issue_data()
    subj = build_edm_subject(issue, data)
    assert subj.startswith("🐊") or subj[0] in "🐊🐉🐢🦕🦗🌻🌵🌴🧃🍵🏜️🗽🚛🎋🔫🔋"
    assert "Mesh · 08.27｜" in subj
    assert "周报" not in subj
    assert "极客公园" not in subj
    assert "…" not in subj
    assert "..." not in subj
    assert len(subj.split("｜", 1)[1]) <= 28


def test_fallback_no_relations():
    line = _fallback_subject_highlight({"keywords": {"groups": [{"title": "公司与人", "items": [{"name": "OpenAI"}]}]}})
    assert line.startswith("本周关键词：")
    assert _subject_valid_highlight(line)


def test_fallback_first_relation():
    data = {
        "relations": [
            {
                "title": "破壳创智",
                "label": "两处记录待核对",
                "teams": ["编辑部", "Global Partnership"],
                "body": "x",
            }
        ]
    }
    line = _fallback_subject_highlight(data)
    assert line == "破壳创智本周在两处记录里出现"


def test_fit_long_llm_style_line():
    from app.edm import _fit_subject_highlight

    data = {
        "relations": [
            {
                "title": "破壳创智",
                "label": "两处记录待核对",
                "teams": ["编辑部", "Global Partnership"],
                "body": "x",
            }
        ]
    }
    long_line = "编辑部和 Global Partnership 各记了破壳创智的一手信息"
    fitted = _fit_subject_highlight(long_line, data=data)
    assert "…" not in fitted
    assert len(fitted) <= 28
    assert fitted.endswith("出现") or "破壳创智" in fitted
