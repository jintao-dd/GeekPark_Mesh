"""publish 条目快照与 item_facts 一致性。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, item_facts


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    try:
        db.init_db(seed=False)
        con = db.connect()
        con.execute(
            "INSERT INTO issues(slug,date_start,date_end,period_label,status) VALUES(?,?,?,?,?)",
            ("2026-test", "2026-01-01", "2026-01-07", "测试期", "published"),
        )
        iid = con.execute("SELECT id FROM issues WHERE slug='2026-test'").fetchone()["id"]
        con.execute(
            "INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,owner_team,blocked) "
            "VALUES(?,0,'编辑部','T1',1,'L1','fact','测试条目','[]','[]','[]','编辑部',0)",
            (iid,),
        )
        con.commit()
        db.snapshot_published_items(con, iid)
        con.execute(
            "UPDATE items SET text='已改 live' WHERE issue_id=?", (iid,),
        )
        con.commit()
        n = item_facts.reindex_item_facts(con, iid)
        con.commit()
        row = con.execute(
            "SELECT text_snippet FROM item_facts WHERE issue_slug='2026-test'"
        ).fetchone()
        assert n == 1
        assert "测试条目" in (row["text_snippet"] or "")
        assert "已改 live" not in (row["text_snippet"] or "")
        con.close()
        print("test_snapshot: ok")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
