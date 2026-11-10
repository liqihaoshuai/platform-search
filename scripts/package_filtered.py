#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 platform-search，自动排除 Python 缓存、运行时凭据和生成报告。

用法：
    python scripts/package_filtered.py
    python scripts/package_filtered.py --out-dir dist

该脚本只读取技能目录并写入 zip，不删除源文件。
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "dist"
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", "dist", ".config", "browser_cookies"}
EXCLUDED_NAMES = {".env", "last-run.json", "last-report.json", "doctor-cache.json"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log"}
EXCLUDED_PREFIXES = ("platforms_",)


def include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if path.name in EXCLUDED_NAMES:
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if any(path.name.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    if path.name.endswith(".zip"):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT), help="zip 输出目录，默认 ./dist")
    parser.add_argument("--name", default="platform-search-skill.zip", help="zip 文件名")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / args.name

    files = sorted(path for path in ROOT.rglob("*") if path.is_file() and include(path))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT))

    print(f"已打包: {output}")
    print(f"文件数: {len(files)}")
    print("已排除: __pycache__、*.pyc、凭据配置、运行时状态、生成报告和旧 zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
