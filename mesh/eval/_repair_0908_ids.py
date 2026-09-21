#!/usr/bin/env python3
"""修复 2026-09-08 期真值失效：旧 item_id（4402–4431）→ 当前快照（4630+）。

背景：该期被重新灌入，旧 item 行已从 items / item_facts / chunk_index 全部删除，
published_json 不含 item id，故无法用 id 直接映射。旧内容仅存于 git 内评测报告的
残留指纹（RECALL_P1_DIAG.json 的 focus_items.snip）。

策略（内容指纹比对，逐条给证据，不静默替换）：
  1) 实体名精确命中（query 里的公司/人/产品名）
  2) 旧 snip 与当前条目的字符二元组重合度
  3) 团队 / 章节约束（gold 语义隐含，如「编辑部选题」「视频号」）
  4) query 词项覆盖

输出：eval/reports/GOLD_0908_ID_REPAIR.json（含候选、置信度、证据）
      不改 gold 文件，供人工确认后再落盘。

用法：
  python eval/_repair_0908_ids.py \
      --chunks  <chunks_dump.json> \
      --diag    eval/reports/RECALL_P1_DIAG.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OLD_SLUG = "2026-09-08"


def _grams(s: str, n: int = 2) -> set[str]:
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", (s or "").lower())
    return {s[i : i + n] for i in range(max(0, len(s) - n + 1))}


def _overlap(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga)


def _load_old_snips(diag_path: Path) -> dict[str, dict]:
    """从旧诊断报告提取 focus_items 的 snip（唯一的旧内容指纹来源）。"""
    out: dict[str, dict] = {}
    if not diag_path.exists():
        return out
    data = json.loads(diag_path.read_text(encoding="utf-8"))
    for case in data.get("cases") or []:
        if str(case.get("slug") or "") != OLD_SLUG:
            continue
        for iid, info in (case.get("focus_items") or {}).items():
            if info.get("exists"):
                out[str(iid)] = {
                    "snip": info.get("snip") or "",
                    "team": info.get("team") or "",
                    "stype": info.get("stype") or "",
                }
    return out


# 每题的内容指纹规格。
#   entities: 实体名，必中其一
#   hard_team: 团队硬约束（命中才算候选）
#   must_kw: 语义必中组；每组内命中任一即可，所有组都须满足（防串题）
#   want: 期望条数（实际不足时下调并标注）
SPECS: dict[str, dict] = {
    "R07": {"entities": ["OdyssLife"], "want": 1, "hard_team": "", "must_kw": [], "note": "众筹节奏"},
    "R08": {"entities": ["FREELANDER", "神行者"], "want": 3, "hard_team": "", "must_kw": [], "note": "怎么定义产品"},
    "R09": {"entities": ["ZenoWell", "未来脑律"], "want": 1, "hard_team": "", "must_kw": [], "note": "是什么"},
    "R10": {"entities": ["程天"], "want": 1, "hard_team": "", "must_kw": [], "note": "外骨骼"},
    "R14": {
        "entities": ["播放"],
        "want": 5,
        "hard_team": "视频号团队",
        "must_kw": [["播放"]],
        "note": "本周播放较好",
    },
    "R15": {
        "entities": ["IFA"],
        "want": 2,
        "hard_team": "编辑部",
        "must_kw": [["IFA"]],
        "note": "选题",
    },
    "R17": {
        "entities": ["牛一鸣", "滴滴"],
        "want": 2,
        "hard_team": "",
        "must_kw": [["智能硬件", "硬件"]],
        "note": "智能硬件",
    },
    "R21": {
        "entities": ["The Verge", "Verge"],
        "want": 2,
        "hard_team": "外部媒体",
        "must_kw": [["手机", "隐私", "phone", "privacy", "trifold"]],
        "note": "手机或隐私",
    },
    "R22": {
        "entities": ["Seattle Times", "Seattle"],
        "want": 1,
        "hard_team": "外部媒体",
        "must_kw": [["起诉", "sue", "lawsuit", "infringement"]],
        "note": "起诉 OpenAI",
    },
    "R29": {"entities": ["腾讯"], "want": 1, "hard_team": "", "must_kw": [], "note": "wb 生态应用接触"},
    "R30": {"entities": ["floatboat"], "want": 1, "hard_team": "", "must_kw": [], "note": "少卿接触记录"},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--diag", default=str(ROOT / "eval" / "reports" / "RECALL_P1_DIAG.json"))
    ap.add_argument("--gold", default=str(ROOT / "eval" / "retrieval_gold_v1.jsonl"))
    args = ap.parse_args()

    chunks = json.loads(Path(args.chunks).read_text(encoding="utf-8"))
    cur = [c for c in chunks if c["issue_slug"] == OLD_SLUG]
    old_snips = _load_old_snips(Path(args.diag))

    gold_rows = [
        json.loads(l)
        for l in Path(args.gold).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    gold_by_id = {r["id"]: r for r in gold_rows}

    report: dict = {
        "old_slug": OLD_SLUG,
        "current_chunk_n": len(cur),
        "old_fingerprints_available": sorted(old_snips.keys()),
        "cases": {},
    }

    for qid, spec in SPECS.items():
        g = gold_by_id.get(qid) or {}
        old_ids = [str(x) for x in (g.get("relevant_items") or [])]
        query = g.get("query") or ""
        note = spec["note"]

        scored = []
        for c in cur:
            blob = f"{c['title']} {c['body']}"
            ent_hit = [e for e in spec["entities"] if e.lower() in blob.lower()]
            if not ent_hit:
                continue
            # 团队硬约束：不满足直接排除（防跨团队串题）
            if spec["hard_team"] and spec["hard_team"] not in (c["owner_team"] or ""):
                continue
            # 语义必中组：每组内命中任一即可，所有组都须满足
            if any(
                not any(k.lower() in blob.lower() for k in grp)
                for grp in spec["must_kw"]
            ):
                continue
            # 旧 snip 指纹（仅部分题有）
            fp = 0.0
            for oid in old_ids:
                if oid in old_snips:
                    fp = max(fp, _overlap(old_snips[oid]["snip"], blob))
            # query 词项覆盖
            qcov = _overlap(query, blob)
            note_cov = _overlap(note, blob)
            team_ok = (not spec["hard_team"]) or (
                spec["hard_team"] in (c["owner_team"] or "")
            )
            score = (
                len(ent_hit) * 3.0
                + fp * 2.0
                + qcov * 1.5
                + note_cov * 1.0
                + (0.6 if team_ok else 0.0)
            )
            scored.append(
                {
                    "item_id": c["item_id"],
                    "chunk_id": c["chunk_id"],
                    "title": c["title"],
                    "team": c["owner_team"],
                    "stype": c["stype"],
                    "ent_hit": ent_hit,
                    "old_snip_overlap": round(fp, 3),
                    "query_cov": round(qcov, 3),
                    "note_cov": round(note_cov, 3),
                    "team_ok": team_ok,
                    "score": round(score, 3),
                    "body": c["body"][:110],
                }
            )
        scored.sort(key=lambda x: -x["score"])

        picked = scored[: spec["want"]]
        report["cases"][qid] = {
            "query": query,
            "note": note,
            "old_relevant_items": old_ids,
            "want": spec["want"],
            "found": len(picked),
            "downgraded": len(picked) < spec["want"],
            "candidates_found": len(scored),
            "picked": picked,
            "alternates": scored[spec["want"] : spec["want"] + 4],
            "confidence": (
                "high"
                if len(scored) >= spec["want"]
                and all(p["score"] >= 4.0 for p in picked)
                else ("medium" if len(scored) >= spec["want"] else "low")
            ),
        }

    out = ROOT / "eval" / "reports" / "GOLD_0908_ID_REPAIR.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"old fingerprints available: {report['old_fingerprints_available'] or '无'}")
    print(f"current {OLD_SLUG} chunks: {len(cur)}")
    for qid, c in report["cases"].items():
        ids = [p["item_id"] for p in c["picked"]]
        flag = "  <-- 条数不足，需人工确认" if c.get("downgraded") else ""
        print(
            f"{qid} [{c['confidence']:<6}] old={c['old_relevant_items']} "
            f"→ new={ids}  (cands={c['candidates_found']}){flag}"
        )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
