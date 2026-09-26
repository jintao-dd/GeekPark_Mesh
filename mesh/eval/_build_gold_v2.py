#!/usr/bin/env python3
"""生成修复后的评测集（item 级 + chunk 级真值）。

输入：
  eval/reports/GOLD_0908_ID_REPAIR.json   指纹比对结果（人工已核对）
  <chunks_dump.json>                      tmesh 当前 item 层 chunk 快照
  eval/retrieval_gold_v1.jsonl            原始 gold

输出：
  eval/retrieval_gold_v2.jsonl            修复后 item 级 gold（30 题）
  eval/chunk_gold_v2.jsonl                chunk 级 gold（真值 = item 真值 1:1 映射）
  eval/reports/GOLD_REPAIR_MANIFEST.json  修复清单（改了什么、依据、置信度）

设计：
  - 2026-8-17 的 item_id 未变，直接沿用；09-08 用指纹比对结果替换
  - item → chunk 为严格 1:1，chunk 真值由 item 真值推导，零人工标注
  - 条数下调的题（R14 5→3、R17 2→1）在 manifest 中显式标注
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True)
    ap.add_argument(
        "--repair", default=str(ROOT / "eval" / "reports" / "GOLD_0908_ID_REPAIR.json")
    )
    ap.add_argument("--gold", default=str(ROOT / "eval" / "retrieval_gold_v1.jsonl"))
    args = ap.parse_args()

    chunks = json.loads(Path(args.chunks).read_text(encoding="utf-8"))
    repair = json.loads(Path(args.repair).read_text(encoding="utf-8"))

    # item_id → chunk_id（严格 1:1，已离线验证）
    item2chunk: dict[tuple[str, int], str] = {}
    # item_id 全库唯一 → 全局索引，供 latest_published（无显式 slug）反查
    item2chunk_global: dict[int, str] = {}
    chunk_meta: dict[str, dict] = {}
    for c in chunks:
        key = (c["issue_slug"], int(c["item_id"]))
        item2chunk[key] = c["chunk_id"]
        item2chunk_global[int(c["item_id"])] = c["chunk_id"]
        chunk_meta[c["chunk_id"]] = c

    def _resolve_chunk(slug: str, iid: str) -> str:
        try:
            n = int(iid)
        except Exception:
            return ""
        if slug and (slug, n) in item2chunk:
            return item2chunk[(slug, n)]
        return item2chunk_global.get(n, "")

    # 09-08 修复映射：qid → 新 item_id 列表
    remap: dict[str, list[int]] = {}
    for qid, c in repair["cases"].items():
        remap[qid] = [int(p["item_id"]) for p in c["picked"]]

    gold_rows = [
        json.loads(l)
        for l in Path(args.gold).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]

    v2_rows: list[dict] = []
    chunk_rows: list[dict] = []
    manifest: dict = {"repaired": [], "unchanged": [], "chunk_truth": {}}

    for r in gold_rows:
        qid = r["id"]
        scope = r.get("scope") or {}
        raw_issue = str(scope.get("issue") or "")
        slug = raw_issue.split(":", 1)[1] if raw_issue.startswith("explicit:") else ""

        old_ids = [str(x) for x in (r.get("relevant_items") or [])]

        if qid in remap:
            new_ids = remap[qid]
            rec = dict(r)
            rec["relevant_items"] = [str(x) for x in new_ids]
            rec["relevant_chunks"] = [
                item2chunk[(repair["old_slug"], i)]
                for i in new_ids
                if (repair["old_slug"], i) in item2chunk
            ]
            rec["gold_revision"] = "v2_id_remap"
            rec["gold_note"] = (
                f"2026-09-08 重灌导致 item_id 迁移；指纹比对映射 "
                f"{old_ids} → {rec['relevant_items']}"
            )
            if "grades" in rec:
                rec["grades"] = {str(i): 1.0 for i in new_ids}
            if "focus_top5" in rec:
                rec["focus_top5"] = rec["relevant_items"][:2]
            v2_rows.append(rec)
            manifest["repaired"].append(
                {
                    "id": qid,
                    "old_items": old_ids,
                    "new_items": rec["relevant_items"],
                    "new_chunks": rec["relevant_chunks"],
                    "count_changed": len(old_ids) != len(new_ids),
                    "confidence": repair["cases"][qid]["confidence"],
                }
            )
        else:
            rec = dict(r)
            # 未变期：补齐 chunk 真值（latest_published 无显式 slug 时用全局反查）
            ch = []
            for iid in rec["relevant_items"]:
                cid = _resolve_chunk(slug, iid)
                if cid:
                    ch.append(cid)
            rec["relevant_chunks"] = ch
            rec["gold_revision"] = "v2_chunk_added"
            v2_rows.append(rec)
            manifest["unchanged"].append(
                {"id": qid, "items": rec["relevant_items"], "chunks": ch}
            )

        # chunk 级 gold
        ch_ids = v2_rows[-1].get("relevant_chunks") or []
        if ch_ids:
            chunk_rows.append(
                {
                    "id": qid,
                    "category": r.get("category"),
                    "query": r.get("query"),
                    "scope": scope,
                    "relevant_chunks": ch_ids,
                    "relevant_items": v2_rows[-1]["relevant_items"],
                    "chunk_titles": [
                        (chunk_meta[c]["title"] or "")[:60] for c in ch_ids
                    ],
                    "chunk_teams": [chunk_meta[c]["owner_team"] for c in ch_ids],
                }
            )
        manifest["chunk_truth"][qid] = ch_ids

    out_v2 = ROOT / "eval" / "retrieval_gold_v2.jsonl"
    out_ch = ROOT / "eval" / "chunk_gold_v2.jsonl"
    out_mf = ROOT / "eval" / "reports" / "GOLD_REPAIR_MANIFEST.json"

    out_v2.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in v2_rows) + "\n",
        encoding="utf-8",
    )
    out_ch.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in chunk_rows) + "\n",
        encoding="utf-8",
    )
    manifest["summary"] = {
        "total": len(v2_rows),
        "repaired": len(manifest["repaired"]),
        "unchanged": len(manifest["unchanged"]),
        "with_chunk_truth": len(chunk_rows),
        "missing_chunk_truth": [
            q for q, v in manifest["chunk_truth"].items() if not v
        ],
        "count_downgraded": [
            x["id"] for x in manifest["repaired"] if x["count_changed"]
        ],
    }
    out_mf.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    s = manifest["summary"]
    print(f"total={s['total']} repaired={s['repaired']} unchanged={s['unchanged']}")
    print(f"with_chunk_truth={s['with_chunk_truth']}")
    print(f"missing_chunk_truth={s['missing_chunk_truth'] or '无'}")
    print(f"count_downgraded={s['count_downgraded'] or '无'}")
    print(f"\nwrote {out_v2}\nwrote {out_ch}\nwrote {out_mf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
