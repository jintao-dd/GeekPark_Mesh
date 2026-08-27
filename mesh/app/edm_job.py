"""EDM 后台发送：上线自动推送（异步，不阻塞 publish）。"""
from __future__ import annotations

import json
import os
import threading
import traceback

from . import db, edm


def enqueue_auto_send(slug: str, *, by: str = "publish") -> None:
    threading.Thread(target=_auto_send, args=(slug, by), daemon=True).start()


def _auto_send(slug: str, by: str) -> None:
    con = None
    try:
        con = db.connect()
        r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
        if not r or r["status"] != "published":
            return
        if not edm.issue_auto_send_enabled(con, r["id"]):
            return
        addrs = edm.parse_addrs(edm.default_to(con))
        if not addrs:
            con.execute(
                "INSERT INTO mail_log(issue_id,to_addr,subject,ok,error) VALUES(?,?,?,?,?)",
                (
                    r["id"],
                    "",
                    f"GeekPark Mesh · 周报 · {r['period_label']}（自动 · {by}）",
                    0,
                    "未配置默认收件人（EDM 管理页或 EDM_DEFAULT_TO）",
                ),
            )
            con.commit()
            return
        data = json.loads(r["published_json"] or "{}")
        base = os.environ.get("MESH_BASE_URL", "")
        logo = os.environ.get("MESH_LOGO_URL", "")
        edm.send_for_issue(
            con,
            dict(r),
            data,
            to_addrs=addrs,
            test=False,
            base_url=base,
            logo_url=logo,
        )
    except Exception:
        traceback.print_exc()
        if con is not None:
            try:
                r = con.execute("SELECT id, period_label FROM issues WHERE slug=?", (slug,)).fetchone()
                if r:
                    con.execute(
                        "INSERT INTO mail_log(issue_id,to_addr,subject,ok,error) VALUES(?,?,?,?,?)",
                        (r["id"], "", f"GeekPark Mesh · auto ({by})", 0, "自动发送异常"),
                    )
                    con.commit()
            except Exception:
                pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
