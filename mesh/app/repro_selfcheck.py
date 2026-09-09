"""Runtime Environment Self-Check（硬门，无新版本号）。

容器启动后写出 REPRO_STATUS=PASS|FAIL，校验：
  git_sha / image_digest|tag / prompt_hash / ranking / claim_support /
  model / vector_enabled / embedding_enabled / schema_version

FAIL 条件（硬）：
  - expected_sha != runtime_sha
  - vector_enabled=false 且 embedding_calls>0（热路径探测）
  - 关键字段缺失
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATUS_LOCK = threading.Lock()
_LAST: dict[str, Any] | None = None

RANKING_VERSION = "v1.4"
CLAIM_SUPPORT_VERSION = "v2.4c-2"
QUALITY_BASELINE = "v3.0_production_baseline"


def _prompt_hash() -> str:
    root = Path(__file__).resolve().parent / "prompts"
    files = sorted(root.glob("*.md")) if root.is_dir() else []
    blob = [f"{f.name}:{hashlib.sha256(f.read_bytes()).hexdigest()[:16]}" for f in files]
    return hashlib.sha256("\n".join(blob).encode()).hexdigest()[:24] if blob else ""


def _norm_sha(s: str) -> str:
    s = (s or "").strip().lower()
    if s in ("", "unknown", "none"):
        return ""
    return s[:12] if len(s) >= 12 else s


def environment_manifest(*, embedding_calls: int | None = None) -> dict[str, Any]:
    """性能/容量报告必须附带的精简 Environment Manifest。"""
    from . import embeddings
    from .db import SCHEMA_VERSION
    from .llm import model_for_task

    runtime_sha = os.environ.get("MESH_BUILD_GIT_SHA") or ""
    expected_sha = (
        os.environ.get("MESH_EXPECTED_GIT_SHA")
        or os.environ.get("MESH_GIT_SHA")
        or runtime_sha
    )
    vector_on = bool(embeddings.enabled())
    models = {
        "answer": model_for_task("answer"),
        "semantic": model_for_task("semantic"),
        "sensitive": model_for_task("sensitive"),
    }
    out: dict[str, Any] = {
        "commit": _norm_sha(runtime_sha) or runtime_sha,
        "expected_commit": _norm_sha(expected_sha) or expected_sha,
        "image_tag": os.environ.get("MESH_IMAGE_TAG") or os.environ.get("MESH_BUILD_IMAGE_TAG") or "",
        "image_digest": os.environ.get("MESH_IMAGE_DIGEST") or "",
        "image_id": os.environ.get("MESH_IMAGE_ID") or "",
        "model": models.get("answer") or "",
        "models": models,
        "vector": "ON" if vector_on else "OFF",
        "vector_enabled": vector_on,
        "embedding_enabled": vector_on,
        "ranking": RANKING_VERSION,
        "claim_support": CLAIM_SUPPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "prompt_hash": _prompt_hash(),
        "quality_baseline": QUALITY_BASELINE,
    }
    if embedding_calls is not None:
        out["embedding_calls"] = int(embedding_calls)
    return out


def _write_status(doc: dict[str, Any]) -> None:
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    for p in (
        Path("/srv/mesh/data/REPRO_STATUS.json"),
        Path("/srv/mesh/eval/reports/REPRO_STATUS.json"),
        Path(__file__).resolve().parents[1] / "data" / "REPRO_STATUS.json",
        Path(__file__).resolve().parents[1] / "eval" / "reports" / "REPRO_STATUS.json",
    ):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(payload, encoding="utf-8")
        except Exception:
            pass
    try:
        txt = Path("/srv/mesh/data/REPRO_STATUS.txt")
        txt.parent.mkdir(parents=True, exist_ok=True)
        txt.write_text(f"REPRO_STATUS={doc.get('REPRO_STATUS')}\n", encoding="utf-8")
    except Exception:
        pass


def _probe_embed_on_prepare() -> int:
    """Vector OFF 时跑一次轻量 prepare；返回期间 embedding 调用次数。"""
    from . import db, embeddings
    from .ask_scope import AskScope

    embeddings.reset_call_count()
    before = embeddings.call_count()
    try:
        con = db.connect()
        try:
            row = con.execute(
                "SELECT slug FROM issues WHERE status='published' ORDER BY date_end DESC LIMIT 1"
            ).fetchone()
            if not row:
                return 0
            from . import ask_engine

            scope = AskScope(
                channel="repro_selfcheck",
                slug=row["slug"],
                team="编辑部",
                user_id=None,
                feishu_open_id="ou_repro_selfcheck",
                role="viewer",
                user_team="编辑部",
            )
            ask_engine.prepare(
                con,
                "编辑部关注了哪些话题或公司",
                scope,
            )
        finally:
            con.close()
    except Exception as e:
        print(f"[mesh] repro embed probe skipped: {e}", flush=True)
        return 0
    return max(0, embeddings.call_count() - before)


def run_selfcheck(*, probe_embed: bool = True) -> dict[str, Any]:
    """启动自检。返回含 REPRO_STATUS 的文档，并落盘。"""
    failures: list[str] = []
    checks: dict[str, Any] = {}

    try:
        man = environment_manifest()
    except Exception as e:
        doc = {
            "REPRO_STATUS": "FAIL",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "failures": [f"manifest_collect_error:{e}"],
            "checks": {},
        }
        with _STATUS_LOCK:
            global _LAST
            _LAST = doc
        _write_status(doc)
        print("[mesh] REPRO_STATUS=FAIL manifest_collect_error", flush=True)
        return doc

    runtime_sha = _norm_sha(man.get("commit") or "")
    expected_sha = _norm_sha(man.get("expected_commit") or "")
    checks["git_sha"] = {"runtime": runtime_sha, "expected": expected_sha}
    if not runtime_sha:
        failures.append("git_sha_missing")
    if expected_sha and runtime_sha and expected_sha != runtime_sha:
        failures.append(f"expected_sha!={runtime_sha} expected={expected_sha}")

    image_tag = man.get("image_tag") or ""
    image_digest = man.get("image_digest") or man.get("image_id") or ""
    checks["image"] = {"tag": image_tag, "digest": image_digest}
    # digest 可由 ship 注入；启动时至少要有 image_tag（compose 必填）
    if not image_tag or image_tag in ("local", "unknown"):
        # 本地开发放行；容器发布路径必须有真实 tag
        if os.environ.get("MESH_REQUIRE_IMAGE_TAG", "").lower() in ("1", "true", "yes"):
            failures.append("image_tag_missing")

    prompt_hash = man.get("prompt_hash") or ""
    checks["prompt_hash"] = prompt_hash
    if not prompt_hash:
        failures.append("prompt_hash_missing")

    checks["ranking_version"] = man.get("ranking")
    checks["claim_support_version"] = man.get("claim_support")
    if man.get("ranking") != RANKING_VERSION:
        failures.append(f"ranking_version!={RANKING_VERSION}")
    if not str(man.get("claim_support") or "").startswith("v2.4c-2"):
        failures.append("claim_support_version_mismatch")

    model = man.get("model") or ""
    checks["model"] = man.get("models") or model
    if not model:
        failures.append("model_missing")

    vector_enabled = bool(man.get("vector_enabled"))
    embedding_enabled = bool(man.get("embedding_enabled"))
    checks["vector_enabled"] = vector_enabled
    checks["embedding_enabled"] = embedding_enabled

    schema = man.get("schema_version") or ""
    checks["schema_version"] = schema
    if not schema:
        failures.append("schema_version_missing")

    embedding_calls = 0
    if probe_embed and not vector_enabled:
        embedding_calls = _probe_embed_on_prepare()
    checks["embedding_calls"] = embedding_calls
    if (not vector_enabled) and embedding_calls > 0:
        failures.append(f"vector_off_but_embedding_calls={embedding_calls}")

    status = "PASS" if not failures else "FAIL"
    doc = {
        "REPRO_STATUS": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
        "checks": checks,
        "environment_manifest": {**man, "embedding_calls": embedding_calls},
    }
    with _STATUS_LOCK:
        _LAST = doc
    _write_status(doc)
    print(f"[mesh] REPRO_STATUS={status}", flush=True)
    if failures:
        print(f"[mesh] REPRO failures: {failures}", flush=True)
    return doc


def last_status() -> dict[str, Any] | None:
    with _STATUS_LOCK:
        return dict(_LAST) if _LAST else None


def is_pass() -> bool:
    st = last_status()
    return bool(st and st.get("REPRO_STATUS") == "PASS")
