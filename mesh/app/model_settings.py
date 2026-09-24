"""模型清单与任务选择（后台「模型」页可增删/切换，DB 为准，env 兜底）。

设计
----
- 清单：`settings` 表键 `llm_models`，JSON 数组，元素为模型 id 字符串。
- 任务选择：`settings` 表键 `llm_task:<task>`，值为模型 id。
- **DB 为空时回落 .env**（`MESH_LLM_MODEL_*` / `MESH_LLM_MODEL`），
  所以旧部署不动也能跑；后台改动即时生效，无需重启。
- 本模块只读写 DB，不 import llm，避免循环依赖。

为何走 DB 而不是改 .env：改 .env 要重启容器、且会与 ship_image 的 env 固化互相打架；
放 DB 后可多进程即时生效，也不影响可复现基线（基线只记 answer 模型）。
"""
from __future__ import annotations

import json
import os
import re

SETTING_MODELS = "llm_models"
TASK_SETTING_PREFIX = "llm_task:"

# 任务键 → .env 兜底变量（与 llm.model_for_task 保持一致）
TASK_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "semantic": ("MESH_LLM_MODEL_SEMANTIC",),
    "answer": ("MESH_LLM_MODEL_ANSWER",),
    "ask": ("MESH_LLM_MODEL_ANSWER", "MESH_LLM_MODEL"),
    "sensitive": ("MESH_LLM_MODEL_SENSITIVE", "MESH_LLM_MODEL_ANSWER"),
    "controller": (
        "MESH_LLM_MODEL_CONTROLLER",
        "MESH_LLM_MODEL_SEMANTIC",
        "MESH_LLM_MODEL",
    ),
    "default": ("MESH_LLM_MODEL",),
}

TASK_LABELS: list[tuple[str, str]] = [
    ("answer", "成文 / Answer"),
    ("semantic", "语义判定"),
    ("sensitive", "敏感 / 对外成文"),
    ("controller", "Colleague Controller"),
    ("ask", "周报问答中间成文"),
    ("default", "默认（未单列的任务）"),
]

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,127}$")


def env_default_model(task: str = "default") -> str:
    keys = TASK_ENV_KEYS.get((task or "default").strip().lower(), ("MESH_LLM_MODEL",))
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return (os.environ.get("MESH_LLM_MODEL") or "").strip()


def valid_model_id(mid: str) -> bool:
    return bool(_MODEL_ID_RE.match((mid or "").strip()))


def _read_models(con) -> list[str]:
    from . import db

    raw = db.get_setting(con, SETTING_MODELS) or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in data:
        mid = str(x or "").strip()
        if mid and mid not in seen and valid_model_id(mid):
            seen.add(mid)
            out.append(mid)
    return out


def _write_models(con, models: list[str]) -> None:
    from . import db

    db.set_setting(con, SETTING_MODELS, json.dumps(models, ensure_ascii=False))


def list_models(con) -> list[str]:
    """当前清单：DB 优先；DB 为空则用 env 默认 + 各任务 env 值去重兜底（只读展示，不落库）。"""
    models = _read_models(con)
    if models:
        return models
    seed: list[str] = []
    for task, _label in TASK_LABELS:
        m = env_default_model(task)
        if m and m not in seed and valid_model_id(m):
            seed.append(m)
    return seed


def ensure_catalog(con) -> list[str]:
    """确保 DB 里已有清单：空则把 env 兜底固化进去，返回固化后的清单。"""
    models = _read_models(con)
    if models:
        return models
    models = list_models(con)
    if models:
        _write_models(con, models)
    return models


def add_model(con, mid: str) -> tuple[bool, str]:
    mid = (mid or "").strip()
    if not valid_model_id(mid):
        return False, "模型 id 不合法（只允许字母数字与 . _ / : + -，最长 128）"
    models = ensure_catalog(con)
    if mid in models:
        return False, "该模型已在清单中"
    models.append(mid)
    _write_models(con, models)
    return True, ""


def delete_model(con, mid: str) -> tuple[bool, str]:
    mid = (mid or "").strip()
    models = ensure_catalog(con)
    if mid not in models:
        return False, "清单里没有这个模型"
    models = [m for m in models if m != mid]
    _write_models(con, models)
    # 清掉仍指向该模型的任务选择，避免留下悬空引用
    for task, _label in TASK_LABELS:
        if get_task_choice(con, task) == mid:
            from . import db

            db.set_setting(con, TASK_SETTING_PREFIX + task, "")
    return True, ""


def get_task_choice(con, task: str) -> str:
    """该任务在 DB 里的显式选择；空串表示未设置（回落 env）。"""
    from . import db

    task = (task or "default").strip().lower()
    return (db.get_setting(con, TASK_SETTING_PREFIX + task) or "").strip()


def set_task_choice(con, task: str, mid: str) -> tuple[bool, str]:
    from . import db

    task = (task or "").strip().lower()
    if task not in TASK_ENV_KEYS:
        return False, "未知任务"
    mid = (mid or "").strip()
    if mid:
        if not valid_model_id(mid):
            return False, "模型 id 不合法"
        if mid not in ensure_catalog(con):
            return False, "该模型不在清单中，请先在上方添加"
    db.set_setting(con, TASK_SETTING_PREFIX + task, mid)
    return True, ""


def effective_model(con, task: str) -> str:
    """该任务当前实际生效的模型：DB 选择优先，否则 env 兜底。"""
    task = (task or "default").strip().lower()
    choice = get_task_choice(con, task)
    if choice:
        return choice
    return env_default_model(task)


def task_rows(con) -> list[dict]:
    """后台展示用：每行的 env 兜底、DB 选择、生效值、是否被覆盖。"""
    rows = []
    for task, label in TASK_LABELS:
        env_m = env_default_model(task)
        choice = get_task_choice(con, task)
        rows.append({
            "task": task,
            "label": label,
            "env_model": env_m,
            "choice": choice,
            "effective": choice or env_m,
            "overridden": bool(choice),
        })
    return rows
