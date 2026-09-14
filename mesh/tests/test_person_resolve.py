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
    {"name": "万东峰", "open_id": "ou_e", "employee_no": "G-345", "source": "org"},
    {"name": "郭冀昂", "open_id": "ou_f", "employee_no": "", "source": "org"},
    {"name": "郭宇祺", "open_id": "ou_g", "employee_no": "", "source": "org"},
    {"name": "靖宇", "open_id": "ou_h", "employee_no": "", "source": "org"},
    {"name": "郑明明", "open_id": "ou_i", "employee_no": "", "source": "org"},
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
    assert hit.source in ("seed", "org_emp", "roster_alias")


def test_resolve_honorific_wan_laoshi():
    r = pr.resolve_people_in_text("万老师", people=PEOPLE, use_org=False)
    assert "万东峰" in r.canonical_names()


def test_resolve_guo_laoshi_via_roster():
    # 通讯录两郭：无别名时不瞎猜；roster 手填「郭老师」→郭冀昂 后应命中
    r = pr.resolve_people_in_text("郭老师", people=PEOPLE, use_org=False)
    assert "郭冀昂" in r.canonical_names()
    assert not any(h.source.startswith("org_surname") for h in r.hits)


def test_resolve_boss_alias_not_stopped():
    pr.load_roster(force=True)
    people = PEOPLE + [
        {"name": "张鹏", "open_id": "ou_ceo", "employee_no": "G-001", "source": "org"},
        {"name": "杜书惠", "open_id": "ou_hr", "employee_no": "", "source": "org"},
    ]
    r = pr.resolve_people_in_text("帮我看看老板最近在忙什么", people=people, use_org=False)
    assert "张鹏" in r.canonical_names()
    r2 = pr.resolve_people_in_text("程程的周报", people=people, use_org=False)
    assert "杜书惠" in r2.canonical_names()


def test_expand_keeps_user_intent():
    r = pr.resolve_people_in_text(
        "帮我看看锦涛和思琪这周周报相关的事",
        people=PEOPLE,
        use_org=False,
    )
    assert "帮我看看" in r.expanded_query
    assert "杜锦涛" in r.expanded_query
    assert "赵思琪" in r.expanded_query


def test_roster_file_loads_and_has_teams():
    data = pr.load_roster(force=True)
    people = data.get("people") or []
    assert len(people) >= 60
    # 至少有人有团队与职位字段
    assert any((p.get("teams") or []) for p in people if isinstance(p, dict))
    assert any(str(p.get("job_title") or "") for p in people if isinstance(p, dict))
    amap = pr.alias_map()
    assert amap.get("锦涛") == "杜锦涛"
    assert amap.get("49") == "彭康林"


def test_filter_known_people_drops_answer_chrome():
    pr.load_roster(force=True)
    kept = pr.filter_known_people(
        ["已上线周报", "10:30–12:00", "杜锦涛", "老板", "缺的那一块"],
        people=PEOPLE + [{"name": "张鹏", "open_id": "ou_ceo", "employee_no": "G-001", "source": "org"}],
    )
    assert "杜锦涛" in kept
    assert "张鹏" in kept
    assert "已上线周报" not in kept
    assert "缺的那一块" not in kept
