"""跨通道合并 · cross-channel-merge

为什么必须有这一步（改动前请先读完）：

同一次沟通会经两条通道到达——聚合器抓来的日历/聚合文档，和部门提交给总裁办的
会议纪要/录音转写。若不合并，同一件事会变成两条独立条目；关系判定看到"多处记录
提到同一家公司"，就把它推进「可同步的关系」。**那是假阳性**，而读者看不出来是错的，
只会觉得系统在瞎报，进而不再信任它。

「可同步的关系」的价值前提是"**不止一个部门**碰到了同一个人或事"。因此：
  - 合并必须发生在关系判定（relation-link / sync-pick）**之前**；
  - 合并只在**同一 owner_team 内部**进行——跨团队的重复正是我们要找的信号，绝不能合掉。

重叠是常态不是异常：不抛异常，不要求管理员手工判重。
"""
from __future__ import annotations
import json
import re
from difflib import SequenceMatcher

#: 文本相似度阈值。低于此不合并。调高更保守（宁可重复），调低更激进（可能合掉不同的事）。
SIM_THRESHOLD = 0.62
#: 实体重合度阈值（交集 / 较小集合）。
ENT_THRESHOLD = 0.5


def _norm(t: str) -> str:
    """去掉标点、空白与常见虚词，只留判重用的骨架。"""
    t = re.sub(r"[\s，。、；：,.;:！!？?（）()「」【】\"'—·]+", "", t or "")
    return t


def _loads(v):
    if isinstance(v, list):
        return v
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except Exception:
        return []


def _ent_overlap(a: list, b: list) -> float:
    sa, sb = {str(x).strip() for x in a if x}, {str(x).strip() for x in b if x}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def same_event(a: dict, b: dict) -> bool:
    """判断两条条目是否描述同一件事。

    条件：同一归属团队 + 实体足够重合 + 文本足够相似。
    三者缺一不合并——宁可漏合（读者看到两条来源），不可错合（丢信息）。
    """
    if (a.get("owner_team") or "") != (b.get("owner_team") or ""):
        return False
    if (a.get("kind") or "fact") != (b.get("kind") or "fact"):
        return False
    if _ent_overlap(_loads(a.get("entities")), _loads(b.get("entities"))) < ENT_THRESHOLD:
        return False
    return SequenceMatcher(None, _norm(a.get("raw_snippet") or a.get("text")), _norm(b.get("raw_snippet") or b.get("text"))).ratio() >= SIM_THRESHOLD


def plan_merge(items: list[dict]) -> list[dict]:
    """返回合并计划：[{keep: id, drop: [id...], source_labels: [...]}]

    主条目选取规则：优先 manual 通道（人写的纪要通常更完整），其次文本更长者。
    """
    groups: list[list[dict]] = []
    for it in items:
        if it.get("blocked"):
            continue
        for g in groups:
            if same_event(g[0], it):
                g.append(it)
                break
        else:
            groups.append([it])

    plan = []
    for g in groups:
        if len(g) < 2:
            continue
        g_sorted = sorted(g, key=lambda x: (0 if (x.get("channel") == "manual") else 1,
                                            -len((x.get("raw_snippet") or x.get("text")) or "")))
        keep, drop = g_sorted[0], g_sorted[1:]
        labels, seen = [], set()
        for x in g_sorted:
            for lb in (_loads(x.get("source_labels")) or [x.get("source_label")]):
                lb = (lb or "").strip()
                if lb and lb not in seen:
                    seen.add(lb)
                    labels.append(lb)
        plan.append({"keep": keep["id"], "drop": [d["id"] for d in drop], "source_labels": labels})
    return plan


def apply_merge(con, issue_id: int) -> dict:
    """执行合并，返回统计供后台显示。"""
    rows = [dict(r) for r in con.execute(
        "SELECT id, owner_team, team, channel, kind, text, raw_snippet, entities, source_label, source_labels, blocked "
        "FROM items WHERE issue_id=? AND merged_into IS NULL", (issue_id,))]
    for r in rows:
        if not r.get("owner_team"):
            r["owner_team"] = r.get("team")
    plan = plan_merge(rows)
    merged = 0
    for p in plan:
        con.execute("UPDATE items SET source_labels=? WHERE id=?",
                    (json.dumps(p["source_labels"], ensure_ascii=False), p["keep"]))
        for did in p["drop"]:
            con.execute("UPDATE items SET merged_into=? WHERE id=?", (p["keep"], did))
            merged += 1
    con.commit()
    return {"groups": len(plan), "merged": merged, "source_lines": sum(len(p["source_labels"]) for p in plan)}
