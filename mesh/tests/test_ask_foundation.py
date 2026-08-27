"""底层问答：auth 用户、重复检测、草稿不泄露索引。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, db, search
from app import ask_query
from app.retriever import _hit_team_allowed


def test_retrieval_query_followup():
    h = [{"role": "user", "content": "具身智能有哪些公司"}, {"role": "assistant", "content": "…"}]
    q = ask_query.retrieval_query("还有哪些", h)
    assert "具身智能" in q


def test_fts_team_filter():
    assert _hit_team_allowed({"title": "某话题", "body": "编辑部讨论了芯片"}, "编辑部")
    assert not _hit_team_allowed({"title": "某话题", "body": "硅谷 BD 团队"}, "编辑部")
    assert _hit_team_allowed({"owner_team": "编辑部", "body": "x"}, "编辑部")
    assert not _hit_team_allowed({"owner_team": "商业化团队", "body": "x"}, "编辑部")


def test_ask_user_from_cookie():
    con = db.connect()
    db.migrate(con)
    try:
        row = con.execute("SELECT username FROM users LIMIT 1").fetchone()
        assert row
        cookie = {"u": row["username"], "r": "viewer", "t": "", "d": "x"}
        user = auth.ask_user(con, cookie)
        assert user and user.get("id")
        assert user["username"] == row["username"]
    finally:
        con.close()


def test_merge_dedup_item():
    a = [{"issue_slug": "s1", "section": "抽取条目", "title": "张三", "body": "会面记录", "item_id": 9, "score": -1.0}]
    b = [{"issue_slug": "s1", "section": "抽取条目", "title": "张三", "body": "会面记录重复", "item_id": 9, "score": -0.5}]
    merged = search.merge_hits(a, b, limit=5)
    assert len(merged) == 1
    assert merged[0]["item_id"] == 9


def test_reindex_skips_items_when_flag_false():
    con = db.connect()
    db.migrate(con)
    try:
        con.execute(
            "INSERT OR IGNORE INTO issues(id,slug,date_start,date_end,period_label,status,published_json) "
            "VALUES (99997,'test-noitem','2026-01-01','2026-01-07','测试','published','{}')"
        )
        con.execute("DELETE FROM items WHERE issue_id=99997")
        con.execute(
            "INSERT INTO items(issue_id,source_id,team,stype,zone,level,kind,text,entities,roles,signals,"
            "source_label,blocked,owner_team,channel) VALUES (99997,1,'编辑部','T2',4,'L1','fact',"
            "'不应进索引','[]','[]','[]','x',0,'编辑部','manual')"
        )
        con.commit()
        db.reindex_issue(con, 99997, items=False)
        con.commit()
        n = con.execute("SELECT COUNT(*) c FROM item_facts WHERE issue_slug='test-noitem'").fetchone()["c"]
        assert n == 0
    finally:
        con.execute("DELETE FROM items WHERE issue_id=99997")
        con.execute("DELETE FROM issues WHERE id=99997")
        con.execute("DELETE FROM chunk_index WHERE issue_slug='test-noitem'")
        con.commit()
        con.close()


if __name__ == "__main__":
    test_retrieval_query_followup()
    test_fts_team_filter()
    test_ask_user_from_cookie()
    test_merge_dedup_item()
    test_reindex_skips_items_when_flag_false()
    print("ok")
