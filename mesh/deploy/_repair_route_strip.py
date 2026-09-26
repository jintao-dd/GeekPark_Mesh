#!/usr/bin/env python3
"""Repair over-stripped details from known pre-clean text, then re-strip with fixed rules."""
from __future__ import annotations

import json
import sys
from datetime import datetime

from app import db
from app.relation_display import attach_reader_flags, split_relations_for_publish
from app.relation_writer import strip_route_meta_copy

REPAIR_BY_TITLE = {
    "戴若犁（Noitom）入群": "Global Partnership 团队记录：新会员邀约：戴若犁，Noitom 创始人 & CEO，入会流程已走完，嘉宾已入群（已确认）；对编辑部采访池、音频播客团队可用",
    "苏昊（复旦 GPI）新发现": "Global Partnership 团队记录：新发现嘉宾：苏昊，复旦大学通用物理智能研究院（GPI）；对编辑部选题、投资团队可用",
    "任少卿（中科大/蔚来）新发现": "Global Partnership 团队记录：新发现嘉宾：任少卿，中科大通用人工智能研究所所长、蔚来汽车高级副总裁及智驾负责人；对编辑部采访池、投资团队可用",
    "王云鹤（基元律动）拟联系": "Global Partnership 团队记录：新发现公司/嘉宾：王云鹤，基元律动创始人，前华为盘古，有意加入前沿社，先从邀请参加活动开始接触（拟联系）；对编辑部、投资团队可用",
    "赵越（仙工智能）关注中": "Global Partnership 团队记录：新发现公司/嘉宾：赵越，仙工智能创始人，机器人大脑第一股，于 2026 年 6 月 24 日在港交所上市，尚未接触，拟尽量取得联系；对编辑部、投资团队可用",
    "Kein Tung（Mentiforce）已沟通": "编辑部：Lilyann 与 Mentiforce CEO Kein Tung 已沟通（8-25），双方约定长期互相对接优秀早期项目创始人、定期同步进展；国内编辑部选题可用得上。",
}

REPAIR_BODY_BY_TITLE = {
    "张岩（Notta）活动嘉宾": "张岩出现在 Global Partnership 团队 AGI Playground 前沿社活动嘉宾名单中，投资团队可承接。",
    "Melody（Sharpa）活动嘉宾": "Melody 出现在 Global Partnership 团队 AGI Playground 前沿社活动嘉宾名单中，投资团队可承接。",
}


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    con = db.connect()
    row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise SystemExit(f"missing {slug}")
    draft = json.loads(row["draft_json"] or "{}")
    cleaned = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        nr = dict(r)
        title = (nr.get("title") or "").strip()
        if title in REPAIR_BY_TITLE:
            nr["details"] = [strip_route_meta_copy(REPAIR_BY_TITLE[title])]
        if title in REPAIR_BODY_BY_TITLE:
            nr["body"] = strip_route_meta_copy(REPAIR_BODY_BY_TITLE[title])
        nr["title"] = strip_route_meta_copy(nr.get("title") or "")
        nr["body"] = strip_route_meta_copy(nr.get("body") or "")
        nr["details"] = [
            strip_route_meta_copy(str(d)) for d in (nr.get("details") or []) if str(d).strip()
        ]
        nr["details"] = [d for d in nr["details"] if d]
        cleaned.append(nr)

    rels = attach_reader_flags(cleaned)
    reader, backlog = split_relations_for_publish(rels)
    draft["relations"] = rels
    draft["_relations_reader"] = reader
    draft["_relations_backlog"] = backlog
    pub = dict(draft)
    pub["relations"] = reader
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute(
        "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
        (json.dumps(draft, ensure_ascii=False), json.dumps(pub, ensure_ascii=False), stamp, int(row["id"])),
    )
    with db.write_lock():
        db.commit_retry(con)
    con.close()
    out = []
    for r in rels:
        t = r.get("title")
        if t in REPAIR_BY_TITLE or t in REPAIR_BODY_BY_TITLE:
            out.append({"title": t, "body": r.get("body"), "details0": (r.get("details") or [""])[0]})
    print(json.dumps({"ok": True, "repaired": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
