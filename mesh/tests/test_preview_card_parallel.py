"""Cards 受控并行：顺序落库、profile 字段、concurrency 夹紧。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import preview_job


def test_card_concurrency_clamp(monkeypatch=None):
    os.environ["MESH_PREVIEW_CARD_CONCURRENCY"] = "99"
    assert preview_job._card_concurrency() == 8
    os.environ["MESH_PREVIEW_CARD_CONCURRENCY"] = "0"
    assert preview_job._card_concurrency() == 1
    os.environ["MESH_PREVIEW_CARD_CONCURRENCY"] = "2"
    assert preview_job._card_concurrency() == 2
    os.environ.pop("MESH_PREVIEW_CARD_CONCURRENCY", None)
    assert preview_job._card_concurrency() == 1


def test_parallel_cards_preserve_apply_order_and_profile():
    """并发完成顺序可乱，但落库顺序必须跟 teams 原序。"""
    teams = ["T_slow", "T_fast", "T_mid"]
    upsert_order: list[str] = []
    lock = threading.Lock()

    def fake_build(team, items, period_label):
        delay = {"T_slow": 0.25, "T_fast": 0.02, "T_mid": 0.08}[team]
        time.sleep(delay)
        return {"title": f"card-{team}", "bullets": [team]}

    prepared = [
        {
            "i": i,
            "team": t,
            "items": [{"text": t, "zone": "①", "kind": "fact", "entities": "[]"}],
            "fp": f"fp-{t}",
            "existing": None,
            "reuse": False,
        }
        for i, t in enumerate(teams)
    ]

    # 直接测 worker + 有序 apply 契约（不启整 job）
    os.environ["MESH_PREVIEW_CARD_CONCURRENCY"] = "3"
    card_conc = preview_job._card_concurrency()
    assert card_conc == 3

    from app.job_resilience import ResilienceReport, try_unit
    from app import preview_progressive as prog

    resilience = ResilienceReport()
    card_profile: list[dict] = []
    built: dict = {}

    def _llm_build(_team, _items):
        return fake_build(_team, _items, "p")

    def _build_one(job):
        team = job["team"]
        local = ResilienceReport()
        t0 = time.perf_counter()
        start_iso = "start"
        card = try_unit(
            lambda: _llm_build(team, job["items"]),
            unit=team,
            kind="card",
            report=local,
        )
        return {
            "team": team,
            "card": card,
            "local": local,
            "profile": {
                "team": team,
                "card_count": 3,
                "card_concurrency": card_conc,
                "card_start": start_iso,
                "card_end": "end",
                "card_latency": round((time.perf_counter() - t0) * 1000.0, 1),
                "card_status": "ok" if card is not None else "skipped",
            },
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_build_one, job): job["team"] for job in prepared}
        for fut in as_completed(futs):
            built[futs[fut]] = fut.result()

    # 故意：完成顺序应是 fast → mid → slow，但 apply 按 teams
    finish_order = sorted(built.keys(), key=lambda t: built[t]["profile"]["card_latency"])
    assert finish_order[0] == "T_fast"

    for job in prepared:
        team = job["team"]
        result = built[team]
        for rec in result["local"].attempts:
            resilience.record(rec)
        card_profile.append(result["profile"])
        card = prog.attach_card_fingerprint(result["card"], job["fp"])
        with lock:
            upsert_order.append(team)
        assert card["title"] == f"card-{team}"

    assert upsert_order == teams
    assert [p["team"] for p in card_profile] == teams
    for p in card_profile:
        assert p["card_status"] == "ok"
        assert p["card_concurrency"] == 3
        assert p["card_count"] == 3
        assert "card_latency" in p
        assert "card_start" in p
        assert "card_end" in p

    os.environ.pop("MESH_PREVIEW_CARD_CONCURRENCY", None)


def test_force_card_rebuild_flag():
    os.environ["MESH_PREVIEW_CARD_FORCE"] = "1"
    assert preview_job._force_card_rebuild() is True
    os.environ.pop("MESH_PREVIEW_CARD_FORCE", None)
    assert preview_job._force_card_rebuild() is False
