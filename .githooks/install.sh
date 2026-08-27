#!/usr/bin/env bash
# 把仓库内 hook 安装到本机 .git/hooks（不改 git config）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.git/hooks"
cp "$ROOT/.githooks/pre-commit" "$ROOT/.git/hooks/pre-commit"
cp "$ROOT/.githooks/pre-commit.ps1" "$ROOT/.git/hooks/pre-commit.ps1"
chmod +x "$ROOT/.git/hooks/pre-commit"
echo "installed: .git/hooks/pre-commit (+ pre-commit.ps1)"
