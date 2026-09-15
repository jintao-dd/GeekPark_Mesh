# -*- coding: utf-8 -*-
from app.relation_writer import (
    enforce_narrative_hygiene,
    title_has_label_leak,
    title_is_formula,
)

t, b, d, f = enforce_narrative_hygiene(
    title="京东：催初稿 × 选题提报",
    body="",
    details=["商业化团队：催初稿", "编辑部：选题提报"],
    candidate_title="京东",
)
print({"title": t, "body": b, "flags": f})
assert f.get("title_rebuilt")
assert "×" not in t
assert not title_is_formula(t)
assert b  # synthesized
assert f.get("body_synthesized_from_details")
print("HYGIENE_V3_OK")
