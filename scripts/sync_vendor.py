#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 platform-search 内置检索模块的完整性。

本版本不从其他技能、宿主工具或外部安装目录同步源码；升级应通过项目
自身的版本管理和测试流程完成。
"""
from __future__ import annotations

import argparse
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent / "engine"
REQUIRED = {
    "domestic": ENGINE_DIR / "domestic" / "lib" / "pipeline.py",
    "global": ENGINE_DIR / "global" / "runner.py",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写文件；保留兼容参数")
    parser.parse_args()
    print("platform-search 内置模块检查")
    failed = False
    for name, path in REQUIRED.items():
        ok = path.is_file()
        print(f"  {name}: {'正常' if ok else '缺失'} - {path}")
        failed |= not ok
    print("检查完成。不会读取或覆盖其他目录。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
