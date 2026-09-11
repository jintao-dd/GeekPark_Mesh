"""Person resolve unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import person_resolve as pr


PEOPLE = [
    {"name": "杜锦涛", "open_id": "ou_a", "employee_no": "1001", "source": "org"},
    {"name": "赵思琪", "open_id": "ou_b", "employee_no": "1002", "source": "org"},
    {"name": "彭康林", "open_id": "ou_c", "employee_no": "49", "source": "org"},
    {"name": "张山山", "open_id": "ou_d", "employee_no": "1004", "source": "org"},
]


def test_resolve_given_name_jintao():
    r = pr.resolve_people_in_text("锦涛最近在忙什么", people=PEOPLE, use_org=False)
    names = r.canonical_names()
    assert "杜锦涛" in names
    assert "杜锦涛" in r.expanded_query
    assert "锦涛" in r.expanded_query  # 原简称仍保留


def test_resolve_siqi():
    r = pr.resolve_people_in_text("思琪的日历", people=PEOPLE, use_org=False)
    assert "赵思琪" in r.canonical_names()


def test_resolve_emp_no_49():
    r = pr.resolve_people_in_text("49最近跟谁聊过", people=PEOPLE, use_org=False)
    assert "彭康林" in r.canonical_names()
    hit = next(h for h in r.hits if h.canonical == "彭康林")
    assert hit.source in ("seed", "org_emp")


def test_expand_keeps_user_intent():
    r = pr.resolve_people_in_text(
        "帮我看看锦涛和思琪这周周报相关的事",
        people=PEOPLE,
        use_org=False,
    )
    assert "帮我看看" in r.expanded_query
    assert "杜锦涛" in r.expanded_query
    assert "赵思琪" in r.expanded_query
