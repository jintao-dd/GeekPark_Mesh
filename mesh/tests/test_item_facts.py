"""item_facts 索引与双轨检索。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app import item_facts, search


def test_row_from_item():
    rec = item_facts.row_from_item(
        {
            "id": 1,
            "source_id": 2,
            "stype": "T3",
            "zone": 3,
            "level": "L1",
            "kind": "fact",
            "text": "与某 AI 公司创始人会面，讨论来华合作。",
            "entities": json.dumps(["张三", "某AI公司"], ensure_ascii=False),
            "roles": "[]",
            "signals": "[]",
            "owner_team": "硅谷 BD 团队",
            "source_label": "硅谷 BD 团队建联记录",
        },
        issue_slug="2026-08-21",
        issue_id=9,
        date_start="2026-08-15",
        date_end="2026-08-21",
    )
    assert rec["primary_name"] == "张三"
    assert rec["owner_team"] == "硅谷 BD 团队"
    assert "张三" in rec["toks"]


def test_reindex_roundtrip():
    con = db.connect()
    db.migrate(con)
    try:
        con.execute(
            "INSERT OR IGNORE INTO issues(id,slug,date_start,date_end,period_label,status,published_json) "
            "VALUES (99999,'test-if','2026-01-01','2026-01-07','测试','published','{}')"
        )
        con.execute("DELETE FROM items WHERE issue_id=99999")
        con.execute(
            "INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,"
            "source_label,blocked,owner_team,channel) VALUES (99999,1,'编辑部','T2',4,'L1','fact',"
            "'选题：具身智能进展','[\"具身智能\"]','[]','[]','编辑部选题表',0,'编辑部','manual')"
        )
        con.commit()
        n = item_facts.reindex_item_facts(con, 99999)
        assert n == 1
        hits = item_facts.search(con, "具身智能")
        assert any(h.get("issue_slug") == "test-if" for h in hits)
        merged = search.hybrid_search(con, "具身智能", slug="test-if", limit=10)
        assert merged
    finally:
        con.execute("DELETE FROM items WHERE issue_id=99999")
        con.execute("DELETE FROM issues WHERE id=99999")
        con.execute("DELETE FROM item_facts WHERE issue_slug='test-if'")
        item_facts.sync_fts(con)
        con.commit()
        con.close()


def test_multi_entity_reindex():
    con = db.connect()
    db.migrate(con)
    try:
        con.execute(
            "INSERT OR IGNORE INTO issues(id,slug,date_start,date_end,period_label,status,published_json) "
            "VALUES (99998,'test-ie','2026-01-01','2026-01-07','测试','published','{}')"
        )
        con.execute("DELETE FROM items WHERE issue_id=99998")
        con.execute(
            "INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,"
            "source_label,blocked,owner_team,channel) VALUES (99998,1,'硅谷 BD 团队','T3',3,'L1','fact',"
            "'与张三、某AI公司讨论合作','[\"张三\",\"某AI公司\"]','[]','[]','BD记录',0,'硅谷 BD 团队','manual')"
        )
        con.commit()
        item_facts.reindex_item_facts(con, 99998)
        rows = con.execute(
            "SELECT entity_name FROM item_entity_facts WHERE issue_slug='test-ie' ORDER BY entity_name"
        ).fetchall()
        names = [r["entity_name"] for r in rows]
        assert names == ["张三", "某AI公司"]
        hits = item_facts.search_entities(con, "某AI")
        assert any(h.get("title") == "某AI公司" for h in hits)
    finally:
        con.execute("DELETE FROM items WHERE issue_id=99998")
        con.execute("DELETE FROM issues WHERE id=99998")
        con.execute("DELETE FROM item_facts WHERE issue_slug='test-ie'")
        con.execute("DELETE FROM item_entity_facts WHERE issue_slug='test-ie'")
        item_facts.sync_fts(con)
        con.commit()
        con.close()


if __name__ == "__main__":
    test_row_from_item()
    test_reindex_roundtrip()
    test_multi_entity_reindex()
    print("ok")
