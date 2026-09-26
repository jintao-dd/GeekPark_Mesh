#!/usr/bin/env python3
"""组织/业务队一致性检查：禁止 identity 与 db 两套 normalize 分叉。

用法：
  PYTHONPATH=. python eval/check_org_team_consistency.py
  PYTHONPATH=. python eval/check_org_team_consistency.py --json

退出码：0 通过；2 发现 split-brain 或人级零业务队。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, ingest  # noqa: E402
from app.agent import identity as idn  # noqa: E402
from app.agent import dept_team_map as dtm  # noqa: E402

# 周报生产队：飞书通讯录可能无独立部门，roster 无人挂载属预期（警告，不 fail）
_WEEKLY_ONLY_OK = frozenset({
    "硅谷 BD 团队",
    "英文站",
    "音频播客团队",
    "视频号团队",
    "外部媒体",
    "内容中心·数据聚合",
    "其他",
})


def _roster_people() -> list[dict]:
    path = ROOT / "app" / "agent" / "data" / "company_people_roster.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(raw.get("people") or [])


def run() -> dict:
    dtm.load_dept_team_map(force=True)
    people = _roster_people()
    raw_teams: set[str] = set()
    for p in people:
        for t in p.get("teams") or []:
            if str(t).strip():
                raw_teams.add(str(t).strip())

    # 部门 map 里的 name / canonical 也要进探针
    for row in dtm.load_dept_team_map().get("departments") or []:
        for k in ("name", "canonical_team", "parent_team"):
            v = str(row.get(k) or "").strip()
            if v:
                raw_teams.add(v)
    for t in ingest.TEAMS:
        raw_teams.add(t)

    split_brain: list[dict] = []
    for t in sorted(raw_teams):
        a = db.normalize_team(t)
        b = idn.normalize_team(t)
        # identity 对占位桶返回 None；db 可能原样返回 ingest.TEAMS 名
        # 业务口径：两边要么都落到同一业务队，要么都「不是身份主队」
        a_biz = a if a and a not in idn._NON_BUSINESS else None
        b_biz = b
        if a_biz != b_biz:
            split_brain.append({"raw": t, "db": a, "identity": b})

    person_fail: list[dict] = []
    covered: set[str] = set()
    for p in people:
        normed: list[str] = []
        for t in p.get("teams") or []:
            n = idn.normalize_team(t)
            if n and n not in normed:
                normed.append(n)
                covered.add(n)
        if not normed:
            person_fail.append({"name": p.get("name"), "teams": p.get("teams")})

    uncovered = [t for t in ingest.TEAMS if t not in covered]
    weekly_only = [t for t in uncovered if t in _WEEKLY_ONLY_OK]
    uncovered_unexpected = [t for t in uncovered if t not in _WEEKLY_ONLY_OK]

    # 海外拓展必须在 TEAMS 且两边归一一致
    haiwai_ok = (
        "海外拓展" in ingest.TEAMS
        and db.normalize_team("海外拓展") == "海外拓展"
        and idn.normalize_team("海外拓展") == "海外拓展"
    )

    out = {
        "n_roster": len(people),
        "n_raw_team_strings": len(raw_teams),
        "split_brain": split_brain,
        "person_no_biz_team": person_fail,
        "weekly_only_uncovered": weekly_only,
        "uncovered_unexpected": uncovered_unexpected,
        "haiwai_unified": haiwai_ok,
        "pass": (
            not split_brain
            and not person_fail
            and not uncovered_unexpected
            and haiwai_ok
        ),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = run()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"roster={out['n_roster']} raw_teams={out['n_raw_team_strings']}")
        print(f"haiwai_unified={out['haiwai_unified']}")
        print(f"split_brain={len(out['split_brain'])}")
        for row in out["split_brain"]:
            print(f"  SPLIT {row}")
        print(f"person_no_biz_team={len(out['person_no_biz_team'])}")
        for row in out["person_no_biz_team"]:
            print(f"  PERSON {row}")
        print(f"weekly_only_uncovered={out['weekly_only_uncovered']}")
        print(f"uncovered_unexpected={out['uncovered_unexpected']}")
        print("PASS" if out["pass"] else "FAIL")
    return 0 if out["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
