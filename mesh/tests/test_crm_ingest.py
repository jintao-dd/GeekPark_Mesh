"""CRM 增量 → T3「硅谷 BD 团队创业者数据库」接入（生成预览前自动拉取）。

覆盖：
- 增量窗口（锚点解耦同步游标、重跑幂等收敛）
- 来源标签、抽取落库、无变动不建来源
- 短行分块（不再 80 行硬截断）
- Take 页面正文进 digest
- status 回报（ok/truncated/no_changes/failed）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import crm_ingest, db


def _seed_issue(con, slug: str = "test-crm-ingest") -> dict:
    con.execute(
        "INSERT OR REPLACE INTO issues(id,slug,date_start,date_end,period_label,status,draft_json) "
        "VALUES (99001,?, '2026-09-16','2026-09-22','测试','draft','{}')",
        (slug,),
    )
    con.execute("DELETE FROM sources WHERE issue_id=99001")
    con.execute("DELETE FROM items WHERE issue_id=99001")
    con.execute("DELETE FROM crm_sync_state")
    con.execute("DELETE FROM crm_people")
    con.execute("DELETE FROM crm_companies")
    con.execute("DELETE FROM crm_interactions")
    con.execute("DELETE FROM crm_takes")
    con.execute("DELETE FROM crm_page_blocks")
    try:
        con.execute("DELETE FROM crm_cross_anchor")
    except Exception:
        pass
    con.commit()
    crm_ingest.ensure_anchor_schema(con)
    con.commit()
    return dict(con.execute("SELECT * FROM issues WHERE id=99001").fetchone())


def test_incremental_window_only_changed_rows():
    con = db.connect()
    try:
        issue = _seed_issue(con)
        con.execute(
            "INSERT INTO crm_people(notion_id,display_name,company_names,headline,last_edited_time) "
            "VALUES('p-old','旧人','A','X','2026-09-19T00:00:00.000Z')"
        )
        con.execute(
            "INSERT INTO crm_people(notion_id,display_name,company_names,headline,last_edited_time) "
            "VALUES('p-new','新人','B','Y','2026-09-21T00:00:00.000Z')"
        )
        con.commit()

        d = crm_ingest.build_digest(con, since_map={"people": "2026-09-20T00:00:00.000Z"})
        assert d["counts"]["people"] == 1, d["counts"]
        assert "新人" in d["text"] and "旧人" not in d["text"]
        assert issue["slug"] == "test-crm-ingest"
    finally:
        con.close()


def test_ingest_writes_t3_source_with_fixed_label(monkeypatch):
    con = db.connect()
    try:
        issue = _seed_issue(con)
        # 只造 takes，其他表空
        con.execute(
            "INSERT INTO crm_takes(notion_id,name,person_names,verdict,last_edited_time) "
            "VALUES('t1','Yu Su','Yu Su','待见面（9/26）','2026-09-22T03:20:00.000Z')"
        )
        con.commit()
        digest = crm_ingest.build_digest(con, since_map={})
        assert digest["text"]

        # 桩掉 LLM 抽取，验证来源/标签/归属落库
        def _fake_extract(issue_, *, source_id, text):
            return [
                {
                    "zone": 3, "level": "L1", "kind": "fact",
                    "text": "与 Yu Su 待见面（9/26）。",
                    "entities": ["Yu Su"], "roles": ["资源"], "signals": [],
                    "source_label": crm_ingest.SOURCE_LABEL, "pointer": "take: t1", "blocked": 0,
                }
            ]

        monkeypatch.setattr(crm_ingest, "_extract_items", _fake_extract)
        out = crm_ingest.ingest_for_issue(con, issue, digest=digest)
        con.commit()
        assert out["ingested"] is True and out["items"] == 1
        src = con.execute("SELECT * FROM sources WHERE id=?", (out["source_id"],)).fetchone()
        assert src["channel"] == "crm"
        assert src["stype"] == "T3"
        assert src["team"] == "硅谷 BD 团队"
        it = con.execute("SELECT * FROM items WHERE source_id=?", (out["source_id"],)).fetchone()
        assert it["source_label"] == "硅谷 BD 团队创业者数据库"
        assert it["owner_team"] == "硅谷 BD 团队"
        assert it["channel"] == "crm"

        # 重复接入不新增来源行
        out2 = crm_ingest.ingest_for_issue(con, issue, digest=digest)
        con.commit()
        n = con.execute(
            "SELECT COUNT(*) c FROM sources WHERE issue_id=99001 AND channel='crm'"
        ).fetchone()["c"]
        assert n == 1 and out2["source_id"] == out["source_id"]
    finally:
        con.close()


def test_no_changes_makes_no_source(monkeypatch):
    con = db.connect()
    try:
        issue = _seed_issue(con)
        monkeypatch.setenv("MESH_CRM_PREVIEW_INGEST", "1")
        digest = {"text": "", "counts": {"people": 0}, "since": {}, "until": "x"}
        out = crm_ingest.ingest_for_issue(con, issue, digest=digest)
        assert out["ingested"] is False and out["reason"] == "no_changes"
        n = con.execute(
            "SELECT COUNT(*) c FROM sources WHERE issue_id=99001 AND channel='crm'"
        ).fetchone()["c"]
        assert n == 0
    finally:
        con.close()


def test_disabled_env_short_circuits(monkeypatch):
    monkeypatch.setenv("MESH_CRM_PREVIEW_INGEST", "0")
    out = crm_ingest.maybe_ingest_for_issue("test-crm-ingest")
    assert out["ingested"] is False and out["reason"] == "disabled"
    assert out["status"] == "disabled"


# ---------------------------------------------------------------- 锚点


def test_window_uses_issue_date_start_when_no_anchor():
    """首次接入：窗口起点 = 本期 date_start，而不是同步游标。"""
    con = db.connect()
    try:
        issue = _seed_issue(con)
        # 同步游标推到很晚，模拟「别人手动同步过」
        con.execute(
            "INSERT INTO crm_sync_state(kind,cursor_last_edited) "
            "VALUES('people','2026-09-23T00:00:00.000Z')"
        )
        con.commit()
        w = crm_ingest.resolve_window(con, issue)
        # date_start 是 2026-09-16，不受同步游标影响
        assert w["people"] == "2026-09-16T00:00:00.000Z", w
    finally:
        con.close()


def test_bare_sync_does_not_eat_window():
    """裸同步推高 crm_sync_state，窗口与「是否会接入」都不受影响。"""
    con = db.connect()
    try:
        issue = _seed_issue(con)
        con.execute(
            "INSERT INTO crm_takes(notion_id,name,person_names,verdict,last_edited_time) "
            "VALUES('t1','Yu Su','Yu Su','v','2026-09-20T00:00:00.000Z')"
        )
        con.commit()
        w1 = crm_ingest.resolve_window(con, issue)
        d1 = crm_ingest.build_digest(con, since_map=w1)
        assert d1["counts"]["takes"] == 1

        # 模拟一次裸同步把游标推到最新
        con.execute(
            "INSERT OR REPLACE INTO crm_sync_state(kind,cursor_last_edited) "
            "VALUES('takes','2026-09-23T05:23:00.000Z')"
        )
        con.commit()
        w2 = crm_ingest.resolve_window(con, issue)
        d2 = crm_ingest.build_digest(con, since_map=w2)
        assert w2 == w1, (w1, w2)
        assert d2["counts"]["takes"] == 1, "裸同步后仍应能捞到本期增量"
    finally:
        con.close()


def test_anchor_advances_only_after_consume_and_rerun_is_idempotent(monkeypatch):
    """消费成功后锚点推进；重跑沿用同一锚点，不回溯放大窗口。"""
    con = db.connect()
    try:
        issue = _seed_issue(con)
        con.execute(
            "INSERT INTO crm_takes(notion_id,name,person_names,verdict,last_edited_time) "
            "VALUES('t1','Yu Su','Yu Su','v','2026-09-20T00:00:00.000Z')"
        )
        con.commit()
        monkeypatch.setattr(
            crm_ingest, "_extract_items",
            lambda issue_, *, source_id, text: [
                {"zone": 3, "level": "L1", "kind": "fact", "text": "x",
                 "entities": [], "roles": [], "signals": [],
                 "source_label": crm_ingest.SOURCE_LABEL, "pointer": "", "blocked": 0}
            ],
        )
        w0 = crm_ingest.resolve_window(con, issue)
        assert w0["takes"] == "2026-09-16T00:00:00.000Z"

        d = crm_ingest.build_digest(con, since_map=w0)
        crm_ingest.ingest_for_issue(con, issue, digest=d)
        con.commit()

        w1 = crm_ingest.resolve_window(con, issue)
        assert w1["takes"] == d["until"], "消费成功应推进锚点"
        # 重跑：锚点不再回到 date_start（旧 min() 行为会永久钉在最早那天）
        assert w1["takes"] > w0["takes"]
        d2 = crm_ingest.build_digest(con, since_map=w1)
        assert d2["counts"]["takes"] == 0, "同一窗口不应被重复消费"
    finally:
        con.close()


# ---------------------------------------------------------------- 分块


def _digest_with_kinds(n_per_kind: int) -> str:
    parts = ["【Notion CRM 增量 · 创业者数据库】", "增量窗口：x ~ y"]
    for i in range(n_per_kind):
        # 短行（<80 字符），正是会被 aggregator 拆段器丢掉的那种
        parts.append(f"- 姓名：人{i}｜公司：公司{i}｜职位：CEO｜城市：SF｜最近接触：2026-09-{i % 28 + 1:02d}")
    return "\n".join(parts)


def test_split_digest_chunks_keeps_short_lines_and_header():
    text = _digest_with_kinds(120)
    chunks = crm_ingest.split_digest_chunks(text)
    assert len(chunks) > 1, "120 行应被切成多块"
    # 所有原始行都要出现在某一块里，一行都不能丢
    all_lines = [ln for c in chunks for ln in c.splitlines() if ln.startswith("- ")]
    assert len(all_lines) == 120, len(all_lines)
    # 每块都带 header，保证抽取有窗口上下文
    for c in chunks:
        assert "Notion CRM 增量" in c
        assert "增量窗口" in c


def test_split_digest_chunks_respects_char_cap():
    text = _digest_with_kinds(200)
    chunks = crm_ingest.split_digest_chunks(text)
    # 字符上限留出 header 余量
    for c in chunks:
        assert len(c) <= crm_ingest._CHUNK_CHARS + 400, len(c)


def test_extract_all_chunks_calls_llm_per_chunk(monkeypatch):
    """分块抽取：每块调一次，且合并后的条目数 = 各块之和。"""
    seen: list[str] = []

    def _fake(issue_, *, source_id, text):
        seen.append(text)
        return [
            {"zone": 3, "level": "L1", "kind": "fact", "text": "t",
             "entities": [], "roles": [], "signals": [],
             "source_label": crm_ingest.SOURCE_LABEL, "pointer": "", "blocked": 0}
        ]

    monkeypatch.setattr(crm_ingest, "_extract_items", _fake)
    items, n_chunks = crm_ingest.extract_all_chunks(
        {"date_start": "2026-09-16", "date_end": "2026-09-22", "period_label": "x"},
        text=_digest_with_kinds(120),
    )
    assert n_chunks == len(seen) and n_chunks > 1
    assert len(items) == n_chunks


def test_extract_all_chunks_survives_one_bad_chunk(monkeypatch):
    """单块失败只跳过该块，不拖垮整次接入。"""
    calls = {"n": 0}

    def _flaky(issue_, *, source_id, text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return [
            {"zone": 3, "level": "L1", "kind": "fact", "text": "t",
             "entities": [], "roles": [], "signals": [],
             "source_label": crm_ingest.SOURCE_LABEL, "pointer": "", "blocked": 0}
        ]

    monkeypatch.setattr(crm_ingest, "_extract_items", _flaky)
    items, n_chunks = crm_ingest.extract_all_chunks(
        {"date_start": "2026-09-16", "date_end": "2026-09-22", "period_label": "x"},
        text=_digest_with_kinds(160),
    )
    assert n_chunks >= 3
    assert len(items) == n_chunks - 1, "只有坏的那块被跳过"


def test_extract_all_chunks_parallel_runs_all_chunks(monkeypatch):
    """并行路径：所有分块都要被跑到（结果顺序不保证，但条数要对）。"""
    seen: list[str] = []
    import threading

    lock = threading.Lock()

    def _fake(issue_, *, source_id, text):
        with lock:
            seen.append(text)
        return [
            {"zone": 3, "level": "L1", "kind": "fact", "text": "t",
             "entities": [], "roles": [], "signals": [],
             "source_label": crm_ingest.SOURCE_LABEL, "pointer": "", "blocked": 0}
        ]

    monkeypatch.setattr(crm_ingest, "_extract_items", _fake)
    monkeypatch.setenv("MESH_CRM_CHUNK_WORKERS", "4")
    items, n_chunks = crm_ingest.extract_all_chunks(
        {"date_start": "2026-09-16", "date_end": "2026-09-22", "period_label": "x"},
        text=_digest_with_kinds(300),
    )
    assert n_chunks == len(seen) and n_chunks > 4
    assert len(items) == n_chunks


def test_chunks_are_capped(monkeypatch):
    """异常超大窗口：分块数被 _MAX_CHUNKS 兜住，不把 LLM 打爆。"""
    monkeypatch.setattr(crm_ingest, "_MAX_CHUNKS", 3)
    monkeypatch.setattr(
        crm_ingest, "_extract_items",
        lambda issue_, *, source_id, text: [
            {"zone": 3, "level": "L1", "kind": "fact", "text": "t",
             "entities": [], "roles": [], "signals": [],
             "source_label": crm_ingest.SOURCE_LABEL, "pointer": "", "blocked": 0}
        ],
    )
    items, n_chunks = crm_ingest.extract_all_chunks(
        {"date_start": "2026-09-16", "date_end": "2026-09-22", "period_label": "x"},
        text=_digest_with_kinds(300),
    )
    assert n_chunks == 3
    assert len(items) == 3


def test_no_hard_row_cap_for_short_rows():
    """回归：旧的 _CAP_PER_KIND=80 会静默丢掉 747 行；现在不再截断。"""
    con = db.connect()
    try:
        _seed_issue(con)
        for i in range(150):
            con.execute(
                "INSERT INTO crm_people(notion_id,display_name,last_edited_time) VALUES(?,?,?)",
                (f"p{i}", f"人{i}", "2026-09-20T00:00:00.000Z"),
            )
        con.commit()
        d = crm_ingest.build_digest(con, since_map={"people": "2026-09-16T00:00:00.000Z"})
        assert d["counts"]["people"] == 150
        assert not d["truncated"], d["truncated"]
        assert d["text"].count("- 姓名：") == 150
    finally:
        con.close()


# ---------------------------------------------------------------- 正文（详细沟通记录）


def test_timeline_blocks_enter_digest():
    """Take 页面正文（逐次沟通/跟进记录 Log）必须进交叉面。"""
    con = db.connect()
    try:
        issue = _seed_issue(con)
        con.execute(
            "INSERT INTO crm_takes(notion_id,name,person_names,verdict,last_edited_time) "
            "VALUES('take-1','Brad (Bodi) Yuan','Brad (Bodi) Yuan','专访完成','2026-09-22T00:00:00.000Z')"
        )
        for i, (bt, tx) in enumerate(
            [
                ("paragraph", "2026-08-24 · GeekPark's offers / next steps"),
                ("table_row", "09-22（当前） | 发布（中+英）+ BD三线"),
            ]
        ):
            con.execute(
                "INSERT INTO crm_page_blocks"
                "(notion_id,owner_kind,block_id,parent_block_id,ord,block_type,text,"
                "page_last_edited,synced_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("take-1", "takes", f"b{i}", "", i, bt, tx, "2026-09-22T00:00:00.000Z", ""),
            )
        con.commit()

        d = crm_ingest.build_digest(con, since_map={"blocks": "2026-09-16T00:00:00.000Z"})
        assert d["counts"]["blocks"] == 1
        assert d["timeline"]["included"] == 1
        assert "详细沟通记录" in d["text"]
        assert "Brad (Bodi) Yuan" in d["text"]
        assert "发布（中+英）+ BD三线" in d["text"], "跟进记录 Log 表行不能丢"
        assert issue["id"] == 99001
    finally:
        con.close()


def test_timeline_respects_page_window():
    """页面未在本期窗口内更新时，正文不重复进 digest。"""
    con = db.connect()
    try:
        _seed_issue(con)
        con.execute(
            "INSERT INTO crm_page_blocks"
            "(notion_id,owner_kind,block_id,parent_block_id,ord,block_type,text,"
            "page_last_edited,synced_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("take-old", "takes", "b0", "", 0, "paragraph", "旧内容",
             "2026-09-01T00:00:00.000Z", ""),
        )
        con.commit()
        d = crm_ingest.build_digest(con, since_map={"blocks": "2026-09-16T00:00:00.000Z"})
        assert d["counts"]["blocks"] == 0
        assert "旧内容" not in d["text"]
    finally:
        con.close()


def test_status_reported_for_no_changes(monkeypatch):
    """无变更时 status 必须是显式的 no_changes（以前是静默的）。"""
    con = db.connect()
    try:
        _seed_issue(con)
    finally:
        con.close()

    monkeypatch.setenv("MESH_CRM_PREVIEW_INGEST", "1")
    monkeypatch.setattr(
        crm_ingest, "sync_then_build",
        lambda con=None, issue=None: {"ok": True, "text": "", "counts": {"people": 0}},
    )
    out = crm_ingest.maybe_ingest_for_issue("test-crm-ingest")
    assert out["status"] == "no_changes"
    assert out["ingested"] is False
    assert "window" in out
