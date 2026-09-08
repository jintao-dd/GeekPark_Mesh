"""Quality v2 shared metrics — Recall / MRR / nDCG / Precision."""
from __future__ import annotations

import math
from typing import Iterable


def recall_at(relevant: set[str], retrieved: list[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def precision_at(relevant: set[str], retrieved: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    got = retrieved[:k]
    if not got:
        return 0.0
    return len(set(got) & relevant) / len(got)


def mrr(relevant: set[str], retrieved: list[str]) -> float:
    if not relevant:
        return 1.0
    for i, x in enumerate(retrieved, 1):
        if x in relevant:
            return 1.0 / i
    return 0.0


def _dcg(rels: list[float], k: int) -> float:
    s = 0.0
    for i, r in enumerate(rels[:k], 1):
        s += (2**r - 1) / math.log2(i + 1)
    return s


def ndcg_at(
    relevant: set[str],
    retrieved: list[str],
    k: int,
    *,
    grades: dict[str, float] | None = None,
) -> float:
    """Binary relevance unless grades map provided (item_id → grade > 0)."""
    if not relevant and not grades:
        return 1.0
    gmap = grades or {x: 1.0 for x in relevant}
    rels = [float(gmap.get(x, 0.0)) for x in retrieved[:k]]
    ideal = sorted((float(gmap.get(x, 0.0)) for x in gmap if gmap.get(x, 0) > 0), reverse=True)
    idcg = _dcg(ideal, k)
    if idcg <= 0:
        return 0.0
    return _dcg(rels, k) / idcg


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0
