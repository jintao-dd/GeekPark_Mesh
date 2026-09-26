"""Diagnose + hard-clear stuck pipeline/preview for one slug."""
from __future__ import annotations

import json
import sys

from app import job_store

SLUG = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"


def dump(kind: str) -> dict:
    st = job_store.get(kind, SLUG, {})
    print(kind, json.dumps({
        "running": st.get("running"),
        "done": st.get("done"),
        "error": st.get("error"),
        "token": st.get("token"),
        "cur": st.get("cur"),
        "extract": (st.get("results") or {}).get("extract"),
        "message": st.get("message"),
        "phase": st.get("phase"),
    }, ensure_ascii=False))
    return st


def clear(kind: str, msg: str) -> None:
    st = job_store.get(kind, SLUG, {})
    st["running"] = False
    st["done"] = False
    st["error"] = msg
    st["final_status"] = "interrupted"
    try:
        st["token"] = int(st.get("token") or 0) + 1
    except Exception:
        st["token"] = 1
    job_store.put(kind, SLUG, st)


def main() -> None:
    print("=== before ===")
    dump("pipeline")
    dump("preview")
    msg = (
        "已强制结束卡住的任务。请硬刷新（Ctrl+F5）关闭弹窗。"
        "本期来源多半已抽取，可直接「生成预览」。"
    )
    clear("pipeline", msg)
    clear("preview", msg)
    print("=== after ===")
    dump("pipeline")
    dump("preview")


if __name__ == "__main__":
    main()
