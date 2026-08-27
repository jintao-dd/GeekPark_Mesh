#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端发布前检查：JS 语法、模板缓存版本、可选线上冒烟。

用法（在 mesh 目录）：
  python scripts/check_frontend.py
  python scripts/check_frontend.py --remote https://mesh.geekpark.ai
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"

JS_FILES = ("app.js", "console.js", "dialog.js")
TEMPLATE_ASSETS = {
    "base.html": ("app.js", "style.css"),
    "issue_console.html": ("console.js",),
}


def _node_check(path: Path) -> None:
    node = shutil.which("node")
    if not node:
        sys.exit("node 未安装：请先安装 Node.js，才能做 JS 语法检查")
    proc = subprocess.run(
        [node, "--check", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        sys.exit(f"JS 语法错误 {path}: {msg}")


def check_local_js() -> None:
    for name in JS_FILES:
        path = STATIC / name
        if not path.is_file():
            continue
        _node_check(path)
        print(f"  ok syntax  {name}")


def _cache_versions(template: Path) -> dict[str, str]:
    text = template.read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r'/static/([^"\'?]+)\?v=([^"\']+)', text)
    }


def check_local_templates() -> None:
    for tpl, required in TEMPLATE_ASSETS.items():
        path = TEMPLATES / tpl
        if not path.is_file():
            continue
        versions = _cache_versions(path)
        if not versions:
            sys.exit(f"{tpl}: 未找到 /static/...?v= 缓存版本号，请更新模板")
        req_versions = {versions[a] for a in required if a in versions}
        if len(req_versions) != 1:
            missing = [a for a in required if a not in versions]
            if missing:
                sys.exit(f"{tpl}: 缺少 /static/{missing[0]}?v=...")
            sys.exit(f"{tpl}: 关键静态资源缓存版本不一致 { {a: versions[a] for a in required} }")
        for asset in required:
            if asset not in versions:
                sys.exit(f"{tpl}: 缺少 /static/{asset}?v=...")
        print(
            f"  ok cache   {tpl} v={next(iter(req_versions))} "
            f"({', '.join(sorted(required))})"
        )


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "mesh-check-frontend/1"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        sys.exit(f"无法访问 {url}: {e}")


def check_remote(base_url: str) -> None:
    base = base_url.rstrip("/")
    local_versions = _cache_versions(TEMPLATES / "base.html")
    app_ver = local_versions.get("app.js")
    css_ver = local_versions.get("style.css")
    if not app_ver or not css_ver:
        sys.exit("本地 base.html 缺少 app.js / style.css 的 ?v= 版本号")
    if app_ver != css_ver:
        sys.exit(f"本地 base.html 中 app.js 与 style.css 版本不一致: {app_ver} vs {css_ver}")

    for asset, ver in (("app.js", app_ver), ("style.css", css_ver)):
        local_path = STATIC / asset
        if not local_path.is_file():
            sys.exit(f"本地缺少 {asset}")
        remote_url = f"{base}/static/{asset}?v={ver}"
        remote_text = _fetch(remote_url)
        local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
        remote_hash = hashlib.sha256(remote_text.encode("utf-8")).hexdigest()
        if local_hash != remote_hash:
            sys.exit(
                f"线上 {asset} 与本地不一致；可能只更新了模板未上传静态文件。"
                f" ({local_hash[:10]} != {remote_hash[:10]})"
            )
        if asset == "app.js":
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(remote_text)
                tmp = Path(f.name)
            try:
                _node_check(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            for needle in ("openModal", "getElementById('modal')"):
                if needle not in remote_text:
                    sys.exit(f"线上 app.js 缺少搜索弹层逻辑: {needle}")
        print(f"  ok remote  {remote_url}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mesh 前端发布前检查")
    ap.add_argument("--remote", metavar="URL", help="部署后线上冒烟，例如 https://mesh.geekpark.ai")
    args = ap.parse_args()

    print("==> 本地 JS 语法")
    check_local_js()
    print("==> 本地模板缓存版本")
    check_local_templates()
    if args.remote:
        print(f"==> 线上冒烟 {args.remote}")
        check_remote(args.remote)
    print("FRONTEND_OK")


if __name__ == "__main__":
    main()
