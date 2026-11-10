#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理 platform-search 的统一用户级配置。

配置只使用一个目录：~/.config/platform-search（可用 --to-dir 或
PLATFORM_SEARCH_CONFIG_DIR 覆盖）。脚本不会自动读取其他软件的配置目录，
也不会打印凭据值。
"""
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_TARGET = Path.home() / ".config" / "platform-search"
HEADER = (
    "# platform-search 用户级配置\n"
    "# 含明文 API Key 与登录 Cookie：不要提交仓库、不要外传。\n"
    "# 所有内置检索模块共用本文件。\n"
)


def _lines(path: Path):
    rows, keys = [], set()
    if not path.is_file():
        return rows, keys
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.partition("=")[0].strip().removeprefix("export ").strip()
        rows.append(line)
        keys.add(key)
    return rows, keys


def normalize_env(source: Path, existing: Path) -> str | None:
    source_rows, source_keys = _lines(source)
    existing_rows, _ = _lines(existing)
    rows = source_rows or existing_rows
    if not rows:
        return None
    seen = set()
    merged = []
    for row in rows + existing_rows:
        key = row.partition("=")[0].strip().removeprefix("export ").strip()
        if key not in seen:
            seen.add(key)
            merged.append(row)
    return HEADER + "\n" + "\n".join(merged) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to-dir", default=str(DEFAULT_TARGET), metavar="DIR", help="目标用户级配置目录")
    parser.add_argument("--from-file", default=None, metavar="FILE", help="可选：导入一份已有 KEY=VALUE 配置文件")
    parser.add_argument("--no-cookies", action="store_true", help="兼容旧参数；统一配置模式下不执行额外复制")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = parser.parse_args()

    target = Path(args.to_dir).expanduser().resolve()
    output = target / ".env"
    source = Path(args.from_file).expanduser().resolve() if args.from_file else output
    text = normalize_env(source, output)
    print(f"目标（用户级）: {target}")
    print(f"配置来源: {source if args.from_file else '当前统一配置'}")
    if text is None:
        print("  未发现可导入配置；保留现有文件。")
    else:
        keys = sorted({line.split("=", 1)[0].strip().removeprefix("export ").strip()
                       for line in text.splitlines() if "=" in line and not line.startswith("#")})
        same = output.is_file() and output.read_text(encoding="utf-8") == text
        action = "已相同" if same else ("将写入" if args.dry_run else "已写入")
        print(f"  [{action}] .env（键：{', '.join(keys) or '无'}）")
        if not same and not args.dry_run:
            target.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8", newline="\n")
    print("browser_cookies 由用户直接放入目标配置目录，不从其他软件目录自动复制。")
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
