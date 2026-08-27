# -*- coding: utf-8 -*-
"""Post-fix regression checks."""
from __future__ import annotations
import datetime
from mesh.app import qa_structured as qs, search, tokenize as tok, auth, db

fails = []

def ok(name):
    print("OK", name)

def fail(name, e):
    fails.append(name)
    print("FAIL", name, "->", e)

# 1 windows
try:
    df, dt, d = qs.parse_window("上周")
    assert df and dt and df <= dt
    df, dt, d = qs.parse_window("上月")
    assert df and dt and df <= dt
    df, dt, d = qs.parse_window("去年")
    assert df.endswith("-01-01") and dt.endswith("-12-31")
    assert qs.parse_window("全部历史") == (None, None, 0)
    df, _, _ = qs.parse_window("看看全部团队")
    assert df is not None
    # 本周闭区间
    df, dt, _ = qs.parse_window("本周")
    assert dt == datetime.date.today().isoformat()
    ok("windows")
except Exception as e:
    fail("windows", e)

# 2 intent
try:
    assert qs.parse_intent("各团队分别知道什么") is None
    i = qs.parse_intent("关于 AI 助听器，公司内部各团队分别知道什么？")
    assert i and i["type"] == "by_team" and i["topic"] == "AI 助听器"
    i = qs.parse_intent(
        "过去一个月里有哪些硬件公司是编辑部接触过、但 Founder Park 团队还没接触过的？"
    )
    assert i["type"] == "diff" and i["team_a"] == "编辑部" and i["team_b"] == "社群"
    assert i.get("hardware") is True
    assert i.get("date_to") is None  # 近一月开上界
    assert qs.parse_intent("编辑部接触过 OpenAI 但这周还没开会 社群也提了") is None
    i = qs.parse_intent("商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？")
    assert i and i["type"] == "intersect"
    assert "投资团队" not in qs.find_teams_in_question("风险投资公司")
    assert "编辑部" in qs.find_teams_in_question("编辑部本周")
    assert "社群" in qs.find_teams_in_question("社群还没接触")
    ok("intent")
except Exception as e:
    fail("intent", e)

# 3 match / lexical
try:
    assert tok.build_match_query("有没有人接触过 OpenAI") == '"openai"'
    m = tok.build_match_query("谁接触过Anthropic") or ""
    assert "anthropic" in m and "接触" not in m
    df, dt = search.lexical_date_range("OpenAI")
    expect = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    assert df == expect and dt is None
    df2, _ = search.lexical_date_range("过去 OpenAI")
    assert df2 == expect  # 裸过去不缩窗
    df3, dt3 = search.lexical_date_range("上周 OpenAI")
    assert dt3 and df3
    ok("match")
except Exception as e:
    fail("match", e)

# 4 feishu / blob
try:
    assert auth.feishu_auto_role({"open_id": "x", "name": "张山山"}) is None
    assert db.teams_from_blob("来源 → 社群 · 商业化团队") == []
    assert "商业化团队" in db.teams_from_blob("商业化团队提交")
    ok("feishu_blob")
except Exception as e:
    fail("feishu_blob", e)

# 5 make_session keys
try:
    tok_s = auth.make_session(
        {"username": "a", "role": "viewer", "team": None, "display": "A", "avatar_url": ""}
    )
    assert tok_s
    ok("session")
except Exception as e:
    fail("session", e)

print("---")
print("FAILS", len(fails), fails)
raise SystemExit(1 if fails else 0)
