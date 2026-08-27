"""retriever 结构化失败回退 hybrid。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, retriever, qa_structured
from app.ask_scope import AskScope


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    try:
        db.init_db(seed=False)
        con = db.connect()
        scope = AskScope(channel="web", user_id=1, role="viewer")
        intent = qa_structured.parse_intent("编辑部与商业化团队的交集")
        assert intent and intent.get("type") != "error"
        mode, hits, meta = retriever.retrieve(con, "编辑部与商业化团队的交集", scope, intent=intent)
        assert mode in ("structured", "hybrid")
        if mode == "hybrid":
            assert meta.get("structured_fallback") is not None
        con.close()
        print("test_retriever: ok")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
