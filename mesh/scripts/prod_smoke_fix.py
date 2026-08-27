# -*- coding: utf-8 -*-
from app import qa_structured as qs, auth, db
df, dt, _ = qs.parse_window("上周")
assert dt, (df, dt)
assert qs.parse_intent("各团队分别知道什么") is None
assert auth.feishu_auto_role({"open_id": "x", "name": "张山山"}) is None
assert db.teams_from_blob("→ 社群") == []
print("PROD_OK", df, dt)
