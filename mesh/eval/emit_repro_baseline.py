#!/usr/bin/env python3
"""Emit Environment / Reproducibility Baseline manifest.

可在本地或容器内跑：
  python eval/emit_repro_baseline.py --out eval/reports/ENV_REPRO_BASELINE.json
  python eval/emit_repro_baseline.py --in-container  # 读镜像内 MESH_BUILD_* / digest 提示
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *cmd],
            cwd=str(ROOT.parent if (ROOT.parent / ".git").exists() else ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _req_pin() -> dict:
    p = ROOT / "requirements.txt"
    lines = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                lines.append(s)
    return {
        "requirements_txt_sha256": _sha256_file(p) if p.exists() else "",
        "pins": lines,
    }


def _prompt_version() -> dict:
    d = ROOT / "app" / "prompts"
    files = sorted(d.glob("*.md")) if d.is_dir() else []
    blob = []
    for f in files:
        blob.append(f"{f.name}:{_sha256_file(f)[:16]}")
    digest = hashlib.sha256("\n".join(blob).encode()).hexdigest()[:24] if blob else ""
    return {"prompt_files": len(files), "prompt_set_sha256_16": digest, "files": blob}


def _gold_version() -> dict:
    out = {}
    for name in (
        "agent_scenario_gold_v3.jsonl",
        "agent_e2e_gold_v1.jsonl",
        "agent_answer_gold_v1.jsonl",
    ):
        p = ROOT / "eval" / name
        if p.exists():
            out[name] = {
                "sha256": _sha256_file(p),
                "bytes": p.stat().st_size,
            }
    return out


def _feature_flags() -> dict:
    keys = [
        "MESH_EMBED_ENABLED",
        "MESH_VECTOR_ENABLED",
        "MESH_AGENT_USE_LLM",
        "MESH_CLAIM_SEMANTIC",
        "MESH_CLAIM_CHECK_MODE",
        "MESH_ALLOW_PROD_PUBLISH",
        "MESH_JOB_INLINE",
    ]
    return {k: (os.environ.get(k) or "") for k in keys}


def _model_env() -> dict:
    keys = [
        "MESH_LLM_PROVIDER",
        "MESH_LLM_BASE_URL",
        "MESH_LLM_MODEL",
        "MESH_LLM_MODEL_ANSWER",
        "MESH_LLM_MODEL_SEMANTIC",
        "MESH_LLM_MODEL_SENSITIVE",
        "MESH_EMBED_BASE_URL",
        "MESH_EMBED_MODEL",
    ]
    # 不采集 API keys
    return {k: (os.environ.get(k) or "") for k in keys}


def collect(*, in_container: bool) -> dict:
    git_sha = os.environ.get("MESH_BUILD_GIT_SHA") or _git(["rev-parse", "HEAD"])
    git_short = (git_sha or "")[:12]
    dirty = _git(["status", "--porcelain"])

    schema = ""
    try:
        from app.db import SCHEMA_VERSION

        schema = SCHEMA_VERSION
    except Exception as e:
        schema = f"error:{e}"

    ranking = "v1.4"
    claim = "v2.4c-2_semantic_extension_frozen"
    retrieval = "lexical_fts_recall_frozen"
    vector = "OFF" if (os.environ.get("MESH_EMBED_ENABLED") or os.environ.get("MESH_VECTOR_ENABLED") or "0").lower() in (
        "0",
        "false",
        "no",
        "off",
        "",
    ) else "ON"

    try:
        from app import embeddings

        vector = "OFF" if not embeddings.enabled() else "ON"
        retrieval_mode_default = embeddings.retrieval_execution_mode(structured_candidate=False)
    except Exception:
        retrieval_mode_default = "unknown"

    try:
        from app.llm import model_for_task

        models = {
            "answer": model_for_task("answer"),
            "semantic": model_for_task("semantic"),
            "sensitive": model_for_task("sensitive"),
        }
    except Exception as e:
        models = {"error": str(e)}

    image_tag = os.environ.get("MESH_IMAGE_TAG") or os.environ.get("MESH_BUILD_IMAGE_TAG") or ""
    image_id = ""
    image_digest = ""
    if in_container:
        # hostname /cgroup 无法直接给 digest；由外部 ship 脚本回填
        image_id = os.environ.get("MESH_IMAGE_ID") or ""
        image_digest = os.environ.get("MESH_IMAGE_DIGEST") or ""

    return {
        "baseline_kind": "environment_reproducibility",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "in_container": in_container,
        "identity": {
            "git_commit": git_sha,
            "git_commit_short": git_short,
            "git_dirty": bool(dirty),
            "image_tag": image_tag,
            "image_id": image_id,
            "image_digest": image_digest,
            "build_date": os.environ.get("MESH_BUILD_DATE") or "",
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "node": "n/a (backend image has no Node runtime requirement)",
        },
        "dependencies": _req_pin(),
        "llm": {
            "env": _model_env(),
            "task_models": models,
        },
        "embedding": {
            "vector": vector,
            "retrieval_execution_mode_default": retrieval_mode_default,
            "model": os.environ.get("MESH_EMBED_MODEL") or "",
            "base_url_set": bool(os.environ.get("MESH_EMBED_BASE_URL")),
        },
        "feature_flags": _feature_flags(),
        "versions": {
            "schema_version": schema,
            "ranking_version": ranking,
            "claim_support_version": claim,
            "retrieval_version": retrieval,
            "quality_baseline": "v3.0_production_baseline",
            "agent_contract": "v1_frozen",
        },
        "prompts": _prompt_version(),
        "gold": _gold_version(),
        "deploy_policy": {
            "forbidden": ["docker cp hot-patch into running app container as release path"],
            "required": [
                "git commit",
                "docker build → image tag + digest",
                "compose up --force-recreate from image",
                "emit this baseline after ship",
            ],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--in-container", action="store_true")
    ap.add_argument("--image-digest", default="")
    ap.add_argument("--image-id", default="")
    ap.add_argument("--image-tag", default="")
    args = ap.parse_args()

    if args.image_digest:
        os.environ["MESH_IMAGE_DIGEST"] = args.image_digest
    if args.image_id:
        os.environ["MESH_IMAGE_ID"] = args.image_id
    if args.image_tag:
        os.environ["MESH_IMAGE_TAG"] = args.image_tag

    doc = collect(in_container=bool(args.in_container))
    out = Path(
        args.out
        or str(ROOT / "eval" / "reports" / "ENV_REPRO_BASELINE.json")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    ident = doc["identity"]
    lines = [
        "# Environment / Reproducibility Baseline",
        "",
        f"- git=`{ident.get('git_commit_short')}` dirty=`{ident.get('git_dirty')}`",
        f"- image_tag=`{ident.get('image_tag')}`",
        f"- image_digest=`{ident.get('image_digest')}`",
        f"- schema=`{doc['versions']['schema_version']}`",
        f"- ranking=`{doc['versions']['ranking_version']}`",
        f"- claim=`{doc['versions']['claim_support_version']}`",
        f"- vector=`{doc['embedding']['vector']}` mode=`{doc['embedding']['retrieval_execution_mode_default']}`",
        f"- python=`{doc['runtime']['python']}`",
        "",
        "See JSON for full pin set. **Release path = commit → build → digest → recreate.**",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(ident, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    print(f"wrote {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
