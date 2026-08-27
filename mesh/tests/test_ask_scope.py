"""AskScope 与会话隔离键。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, ask_scope


def test_scope_keys_isolated():
    con = db.connect()
    db.migrate(con)
    try:
        s1 = ask_scope.AskScope(channel="web", user_id=1, web_session_id="abc")
        s2 = ask_scope.AskScope(channel="web", user_id=1, web_session_id="xyz")
        assert s1.scope_key != s2.scope_key

        g1 = ask_scope.AskScope(channel="feishu_group", chat_id="oc_123")
        g2 = ask_scope.AskScope(channel="feishu_group", chat_id="oc_123", thread_id="msg_9")
        assert g1.scope_key != g2.scope_key

        i1 = ask_scope.AskScope(channel="web", user_id=1, web_session_id="abc", slug="2026-08-21")
        i2 = ask_scope.AskScope(channel="web", user_id=1, web_session_id="abc", slug="2026-08-14")
        assert i1.scope_key != i2.scope_key

        d1 = ask_scope.AskScope(channel="feishu_dm", feishu_open_id="ou_a")
        d2 = ask_scope.AskScope(channel="feishu_dm", feishu_open_id="ou_b")
        assert d1.scope_key != d2.scope_key
    finally:
        con.close()


def test_resolve_team_from_binding():
    con = db.connect()
    db.migrate(con)
    try:
        con.execute(
            "INSERT OR REPLACE INTO feishu_chat_bindings(chat_id, team, label) VALUES ('oc_test','编辑部','测试群')"
        )
        con.commit()
        scope = ask_scope.resolve(con, {"id": 1, "role": "viewer"}, {"channel": "feishu_group", "chat_id": "oc_test"})
        assert scope.team_filter == "编辑部"
    finally:
        con.execute("DELETE FROM feishu_chat_bindings WHERE chat_id='oc_test'")
        con.commit()
        con.close()


if __name__ == "__main__":
    test_scope_keys_isolated()
    test_resolve_team_from_binding()
    print("ok")
