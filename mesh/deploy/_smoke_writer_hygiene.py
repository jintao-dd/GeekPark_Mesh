# -*- coding: utf-8 -*-
from app.relation_writer import (
    body_restates_details,
    enforce_narrative_hygiene,
    title_has_label_leak,
    title_is_formula,
)

t, b, d, f = enforce_narrative_hygiene(
    title="京东：催初稿 × 选题提报",
    body="商业化在催初稿；编辑部选题已提报。",
    details=["商业化团队：催初稿", "编辑部：选题提报"],
    candidate_title="京东",
)
print({"title": t, "body": b, "flags": f, "formula": title_is_formula(t), "leak": title_has_label_leak(t)})
assert f.get("title_rebuilt")
assert f.get("body_cleared_restates") or f.get("body_cleared_template")
assert "×" not in t
assert not title_is_formula(t)
assert body_restates_details(
    "商业化在催初稿；编辑部选题已提报。",
    ["商业化团队：催初稿", "编辑部：选题提报"],
)
print("HYGIENE_V2_OK")
