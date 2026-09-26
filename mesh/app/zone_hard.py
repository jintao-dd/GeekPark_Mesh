"""⑤区 / L3 确定性硬拦（不单信 LLM 自标）。"""
from __future__ import annotations

import re

# 人与钱：融资金额、条款、谈判、薪酬等
_ZONE5_PATTERNS = [
    re.compile(p)
    for p in (
        r"融资.{0,16}(\d|轮|额|美元|美金)|(\d|轮).{0,12}融资",
        r"(估值|投前估值|投后估值).{0,12}\d|\d.{0,12}(估值|亿美元|万人民币)",
        r"\d+(\.\d+)?\s*(亿|万)\s*(美元|美金|人民币|元)",
        r"(A|B|C|D|E|Pre-?A|Pre-?B|种子|天使)\s*轮",
        r"对赌|清算优先|反稀释|董事会席位|领售权|回购条款",
        r"谈判底线|报价策略|还价|压价|排他协议",
        r"年薪|月薪|期权包|薪资包|薪酬|工资\s*\d|股权激励比例",
        r"裁员名单|劝退|协商解除补偿",
    )
]

_L3_PATTERNS = [
    re.compile(p)
    for p in (
        r"内部机密|严禁外传|仅限董事会",
        r"未公开财务|财报初稿",
    )
]


def hard_block_reason(text: str, *, zone: int | None = None, level: str | None = None) -> str | None:
    """返回拦截原因；无需拦截则 None。"""
    blob = text or ""
    if zone == 5 or (level or "").upper() == "L3":
        return "model_flag"
    for pat in _ZONE5_PATTERNS:
        if pat.search(blob):
            return f"zone5:{pat.pattern[:24]}"
    for pat in _L3_PATTERNS:
        if pat.search(blob):
            return f"l3:{pat.pattern[:24]}"
    return None


def apply_hard_blocks(items: list[dict]) -> list[dict]:
    """就地/拷贝后打 blocked；保留原字段。"""
    out: list[dict] = []
    for it in items:
        row = dict(it)
        reason = hard_block_reason(
            " ".join([row.get("text") or "", row.get("raw_snippet") or ""]),
            zone=int(row.get("zone") or 0) or None,
            level=row.get("level"),
        )
        if reason:
            row["blocked"] = 1
            row["zone"] = 5 if reason.startswith("zone5") or reason == "model_flag" and int(row.get("zone") or 0) == 5 else row.get("zone")
            if reason.startswith("l3") or (row.get("level") or "").upper() == "L3":
                row["level"] = "L3"
                row["blocked"] = 1
            row["_block_reason"] = reason
        out.append(row)
    return out
