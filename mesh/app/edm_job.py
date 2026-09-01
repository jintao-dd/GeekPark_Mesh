"""EDM 自动发送：状态写入 mesh_jobs，可重试、可查询。"""
from __future__ import annotations

import json
import os
import time
import traceback

from . import db, edm, job_store

KIND = "edm"


def _defaults(slug: str) -> dict:
    return {
        "slug": slug,
        "running": False,
        "done": False,
        "error": None,
        "phase": "",
        "message": "",
        "token": 0,
        "attempts": 0,
    }


def get_state(slug: str) -> dict:
    return job_store.get(KIND, slug, _defaults(slug))


def enqueue_auto_send(slug: str, *, by: str = "publish", retries: int = 2) -> dict:
    from . import job_runtime

    new_st = _defaults(slug)
    new_st.update({
        "phase": "queued",
        "message": f"排队发送（{by}）",
        "by": by,
        "retries": retries,
    })
    claimed = job_store.try_claim(KIND, slug, _defaults(slug), new_st, force=False)
    if not claimed:
        return get_state(slug)
    token = int(claimed.get("token") or 0)
    job_runtime.spawn_after_claim(
        KIND, slug, _defaults(slug), _auto_send, (slug, by, retries, token),
    )
    return get_state(slug)


def _set(slug: str, token: int, **kw) -> None:
    st = get_state(slug)
    if int(st.get("token") or 0) != int(token):
        return
    st.update(kw)
    job_store.put(KIND, slug, st)


def _auto_send(slug: str, by: str, retries: int, token: int) -> None:
    attempt = 0
    last_err = ""
    while attempt <= max(0, retries):
        if int(get_state(slug).get("token") or 0) != int(token):
            return
        attempt += 1
        _set(slug, token, running=True, phase="sending", message=f"发送中（第 {attempt} 次）", attempts=attempt)
        con = None
        try:
            con = db.connect()
            r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
            if not r or r["status"] != "published":
                _set(slug, token, running=False, done=True, error=None, phase="skip", message="未上线，跳过")
                return
            if not edm.issue_auto_send_enabled(con, r["id"]):
                _set(slug, token, running=False, done=True, phase="disabled", message="未开启自动推送")
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
                _set(slug, token, running=False, done=False, error="未配置默认收件人", phase="error")
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
            _set(slug, token, running=False, done=True, error=None, phase="done", message="发送成功")
            return
        except Exception as e:
            last_err = str(e)[:300]
            traceback.print_exc()
            if attempt <= retries:
                time.sleep(min(8, 2 * attempt))
                continue
            if con is not None:
                try:
                    r = con.execute("SELECT id, period_label FROM issues WHERE slug=?", (slug,)).fetchone()
                    if r:
                        con.execute(
                            "INSERT INTO mail_log(issue_id,to_addr,subject,ok,error) VALUES(?,?,?,?,?)",
                            (r["id"], "", f"GeekPark Mesh · auto ({by})", 0, f"自动发送异常(重试{retries}次): {last_err}"),
                        )
                        con.commit()
                except Exception:
                    pass
            _set(slug, token, running=False, done=False, error=last_err or "发送失败", phase="error")
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
