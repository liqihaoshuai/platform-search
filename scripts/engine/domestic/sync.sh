#!/usr/bin/env bash
# sync.sh - 部署 platform-search 技能到各 Agent 平台
# Author: Jesse (https://github.com/Jesseovo)
# 用法: bash scripts/sync.sh  (从仓库根目录运行)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
echo "源目录: $SRC"

TARGETS=(
  "$HOME/.claude/skills/platform-search"
  "$HOME/.agents/skills/platform-search"
  "$HOME/.codex/skills/platform-search"
)

for t in "${TARGETS[@]}"; do
  echo ""
  echo "--- 同步到 $t ---"
  mkdir -p "$t/scripts/lib"

  cp "$SRC/SKILL.md" "$t/"

  rsync -a "$SRC/scripts/platform_search.py" "$t/scripts/"
  rsync -a "$SRC/scripts/lib/"*.py "$t/scripts/lib/"

  if [ -d "$SRC/fixtures" ]; then
    mkdir -p "$t/fixtures"
    rsync -a "$SRC/fixtures/" "$t/fixtures/"
  fi

  mod_count=$(ls "$t/scripts/lib/"*.py 2>/dev/null | wc -l | tr -d ' ')
  echo "  已复制 $mod_count 个模块"
done

echo ""
echo "同步完成。"
