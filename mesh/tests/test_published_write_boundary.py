"""硬边界：非 Publish 不得 UPDATE published_json。

1) 静态扫描 mesh/app：除白名单外禁止源码出现 UPDATE…published_json
2) 运行时：MeshConnection 在无 allow_published_write 时拒绝该类 SQL
3) weak_relations/resolve 等编辑 API 不得改动 published_json
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# 唯一允许在源码里写出 UPDATE…published_json 的文件（实现入口）
_WHITELIST_FILES = {
    "publish_lane.py",  # write_publish_projection
}

_UPDATE_PUB = re.compile(
    r"UPDATE\s+issues\s+SET[\s\S]{0,400}?published_json",
    re.IGNORECASE,
)


@contextmanager
def _temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app import db, db_conn

    old_env = os.environ.get("MESH_DB")
    old_url = os.environ.get("MESH_DB_URL")
    old_path = db_conn.DB_PATH
    old_mesh_url = db_conn.MESH_DB_URL
    old_db_path = getattr(db, "DB_PATH", None)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    db_conn.DB_PATH = path
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = path
    db.init_db(seed=False)
    try:
        yield path
    finally:
        if old_env is None:
            os.environ.pop("MESH_DB", None)
        else:
            os.environ["MESH_DB"] = old_env
        if old_url is None:
            os.environ.pop("MESH_DB_URL", None)
        else:
            os.environ["MESH_DB_URL"] = old_url
        db_conn.DB_PATH = old_path
        db_conn.MESH_DB_URL = old_mesh_url or ""
        if old_db_path is not None:
            db.DB_PATH = old_db_path
        try:
            os.unlink(path)
        except OSError:
            pass


def test_static_app_sources_no_rogue_published_json_update():
    """扫描 app/*.py：除 publish_lane 外不得出现 UPDATE issues SET … published_json。"""
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name in _WHITELIST_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        # 去掉文档字符串与注释中的误报：只扫字符串字面量里的 SQL
        try:
            tree = ast.parse(text)
        except SyntaxError:
            if _UPDATE_PUB.search(text):
                offenders.append(f"{path.name}: (parse fail, raw match)")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _UPDATE_PUB.search(node.value):
                    offenders.append(f"{path.relative_to(APP_ROOT)}: {node.value[:80]!r}")
            elif isinstance(node, ast.JoinedStr):
                # f-string：拼接后难判；若含 published_json 与 UPDATE 关键字则报
                raw = ast.get_source_segment(text, node) or ""
                if "published_json" in raw and re.search(r"UPDATE\s+issues", raw, re.I):
                    offenders.append(f"{path.relative_to(APP_ROOT)}: f-string {raw[:80]!r}")
    assert not offenders, "非 Publish 源码不得 UPDATE published_json:\n" + "\n".join(offenders)


def test_runtime_guard_blocks_update_without_allow():
    with _temp_db():
        from app import db
        from app.publish_lane import PublishedWriteForbidden, allow_published_write

        con = db.connect()
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
            "VALUES(?,?,?,?,?,?,?)",
            ("gate-pub", "2026-01-01", "2026-01-07", "t", "published", '{"a":1}', '{"a":1}'),
        )
        con.commit()
        iid = con.execute("SELECT id FROM issues WHERE slug='gate-pub'").fetchone()["id"]

        with pytest.raises(PublishedWriteForbidden):
            con.execute(
                "UPDATE issues SET published_json=?, updated_at=? WHERE id=?",
                ('{"hack":1}', "2026-01-08", iid),
            )

        with allow_published_write("test"):
            con.execute(
                "UPDATE issues SET published_json=?, updated_at=? WHERE id=?",
                ('{"ok":1}', "2026-01-08", iid),
            )
        con.commit()
        row = con.execute("SELECT published_json FROM issues WHERE id=?", (iid,)).fetchone()
        assert json.loads(row["published_json"]) == {"ok": 1}
        con.close()


def test_weak_relations_resolve_draft_only():
    """编辑 API 不得改 published_json（运行时 + 行为）。"""
    with _temp_db():
        from app import db
        from app.publish_lane import write_draft_json

        con = db.connect()
        pub = {
            "relations": [
                {"decision_tier": "strong", "title": "LIVE", "body": "b", "evidence": [{}], "needs_review": False},
            ],
        }
        draft = {
            "relations": [
                {
                    "decision_tier": "strong",
                    "title": "DRAFT_WEAK",
                    "body": "b",
                    "evidence": [{}],
                    "needs_review": True,
                    "weak": True,
                },
            ],
        }
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "weak-only",
                "2026-01-01",
                "2026-01-07",
                "t",
                "published",
                json.dumps(draft, ensure_ascii=False),
                json.dumps(pub, ensure_ascii=False),
            ),
        )
        con.commit()
        iid = con.execute("SELECT id FROM issues WHERE slug='weak-only'").fetchone()["id"]

        # 模拟 resolve：只写 draft（与 main.issue_weak_relations_resolve 一致）
        draft2 = json.loads(con.execute("SELECT draft_json FROM issues WHERE id=?", (iid,)).fetchone()["draft_json"])
        draft2["relations"][0]["needs_review"] = False
        write_draft_json(con, iid, json.dumps(draft2, ensure_ascii=False), "2026-01-08 12:00")
        con.commit()

        row = con.execute(
            "SELECT draft_json, published_json FROM issues WHERE id=?", (iid,),
        ).fetchone()
        assert json.loads(row["draft_json"])["relations"][0]["needs_review"] is False
        assert json.loads(row["published_json"])["relations"][0]["title"] == "LIVE"
        con.close()


def test_preview_partial_write_blocked_if_dual_write_attempted():
    """若有人把 preview 又改回双写，运行时守卫应直接炸掉。"""
    with _temp_db():
        from app import db
        from app.publish_lane import PublishedWriteForbidden

        con = db.connect()
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status,draft_json,published_json) "
            "VALUES(?,?,?,?,?,?,?)",
            ("prev-dual", "2026-01-01", "2026-01-07", "t", "draft", "{}", "{}"),
        )
        con.commit()
        iid = con.execute("SELECT id FROM issues WHERE slug='prev-dual'").fetchone()["id"]
        with pytest.raises(PublishedWriteForbidden):
            con.execute(
                "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
                ("{}", '{"leak":1}', "t", iid),
            )
        con.close()
