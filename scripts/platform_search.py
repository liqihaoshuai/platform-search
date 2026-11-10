#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""platform-search —— 跨平台互联网检索（默认自 2018 年起，不受 30 天限制）。

一次调用检索 21 个来源：国内 8 个内容平台、海外 7 个内容平台、6 个公开搜索引擎。
结果按来源分组、组内按发布时间排序、每来源最多 50 条，输出 md/json/html。

实现：内置抓取引擎源码（scripts/engine/）+ 自建零依赖搜索引擎层（web_engines.py）；
配置和输出目录均按用户目录动态解析，也可通过命令行或环境变量覆盖，不依赖固定宿主平台安装目录。

仅供学习、研究与个人知识整理。请遵守各平台服务条款，控制请求频率。
"""

import argparse
import html as html_lib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

# 公开搜索引擎层：自建零依赖实现。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import web_engines  # noqa: E402

HOME = Path.home()

# 用户级配置层：内置检索模块共用当前用户级 platform-search 配置目录（按 HOME 定位，
# 换电脑/换用户名自动跟随，不写死宿主平台或个人路径）。结构见 SKILL.md 2.3。
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_ROOT = HOME / ".config" / "platform-search"

# 单一配置源：平台只使用本项目内置的检索模块。
ENGINE_DIR = Path(__file__).resolve().parent / "engine"


def resolve_cn_scripts() -> Path:
    """返回项目内置的国内检索模块目录。"""
    return ENGINE_DIR / "domestic"


# 浏览器相关的环境变量由内置引擎读取；本技能只在完全未配置时做一次系统浏览器探测，
# 避免新机器必须手动填写 PLATFORM_SEARCH_BROWSER_PATH 才能用需要浏览器的平台。
BROWSER_PATH_ENV = "PLATFORM_SEARCH_BROWSER_PATH"
BROWSER_CHANNEL_ENV = "PLATFORM_SEARCH_BROWSER_CHANNEL"
DISABLE_BROWSER_ENV = "PLATFORM_SEARCH_DISABLE_BROWSER"
BROWSER_CONCURRENCY_ENV = "PLATFORM_SEARCH_BROWSER_CONCURRENCY"


def _managed_playwright_browser() -> tuple:
    """检查 Playwright 自带浏览器是否可用，返回 (是否有 Chromium, 是否有 headless shell)。

    新版 Playwright 的 headless=True 默认使用 headless shell；只装了 chromium
    而缺 headless shell 时，抓取仍会因为找不到可执行文件而失败。
    """
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if root:
        base = Path(root).expanduser()
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) / "ms-playwright" if local else HOME / "AppData" / "Local" / "ms-playwright"
    elif sys.platform == "darwin":
        base = HOME / "Library" / "Caches" / "ms-playwright"
    else:
        base = HOME / ".cache" / "ms-playwright"
    if not base.is_dir():
        return False, False

    chromium_rels = ("chrome-win64/chrome.exe", "chrome-win/chrome.exe",
                     "chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium")
    headless_rels = ("chrome-headless-shell-win64/chrome-headless-shell.exe",
                     "chrome-headless-shell-linux64/chrome-headless-shell",
                     "chrome-headless-shell-mac-x64/chrome-headless-shell",
                     "chrome-headless-shell-mac-arm64/chrome-headless-shell")
    has_chromium = has_headless = False
    for entry in base.glob("*"):
        if not entry.is_dir():
            continue
        name = entry.name.lower()
        rels = headless_rels if ("headless" in name) else (chromium_rels if name.startswith("chromium") else ())
        for rel in rels:
            if (entry / rel).is_file():
                if "headless" in name:
                    has_headless = True
                else:
                    has_chromium = True
                break
    return has_chromium, has_headless


def _system_browser_candidates():
    """按平台返回常见 Chrome / Edge / Chromium 可执行文件位置。"""
    if os.name == "nt":
        roots = []
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(key, "").strip()
            if value:
                roots.append(Path(value))
        names = (
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
        )
        for root in roots:
            for parts in names:
                yield root.joinpath(*parts)
        return
    if sys.platform == "darwin":
        mac = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        )
        for item in mac:
            yield Path(item)
        return
    for item in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                 "/usr/bin/chromium", "/usr/bin/chromium-browser",
                 "/usr/bin/microsoft-edge", "/snap/bin/chromium"):
        yield Path(item)


def _configured_browser_setting() -> dict:
    """读取 platform-search 用户级 .env 中的浏览器配置。"""
    keys = (BROWSER_PATH_ENV, BROWSER_CHANNEL_ENV, DISABLE_BROWSER_ENV, BROWSER_CONCURRENCY_ENV)
    found = {}
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            found[key] = value
    if len(found) == len(keys):
        return found
    env_files = [
        Path(os.environ.get("PLATFORM_SEARCH_CONFIG_DIR", "")).expanduser() / ".env"
        if os.environ.get("PLATFORM_SEARCH_CONFIG_DIR") else DEFAULT_CONFIG_ROOT / ".env",
    ]
    for env_file in env_files:
        if not env_file.is_file():
            continue
        values = _read_env_file(env_file)
        for key in keys:
            if key not in found and values.get(key, "").strip():
                found[key] = values[key].strip()
    return found


def ensure_browser_config(quiet: bool = True) -> None:
    """在完全没配置浏览器时，尝试复用系统已有的 Chrome/Edge。

    优先级：用户显式配置（环境变量或 .env）> Playwright 自带 Chromium（含 headless shell）
    > 系统浏览器。只在三者都不成立时才提示，避免覆盖用户的选择。
    """
    configured = _configured_browser_setting()
    if configured.get(DISABLE_BROWSER_ENV, "").lower() in ("1", "true"):
        return
    if configured.get(BROWSER_PATH_ENV) or configured.get(BROWSER_CHANNEL_ENV):
        return
    has_chromium, has_headless = _managed_playwright_browser()
    if has_headless:
        return
    for candidate in _system_browser_candidates():
        if candidate.is_file():
            os.environ[BROWSER_PATH_ENV] = str(candidate)
            if not quiet:
                reason = "缺少 Playwright headless shell" if has_chromium else "未检测到 Playwright 自带浏览器"
                print(f"提示：{reason}，已自动改用系统浏览器：{candidate}", file=sys.stderr)
            return
    if not quiet:
        print("提示：未找到可用浏览器；如需要浏览器模式的平台，请安装 "
              "`python -m playwright install chromium`，或设置 "
              f"{BROWSER_PATH_ENV} 指向本机 Chrome/Edge。", file=sys.stderr)


def browser_mode_label() -> str:
    """给 dry-run / 状态输出用的一句话描述。"""
    configured = _configured_browser_setting()
    if configured.get(DISABLE_BROWSER_ENV, "").lower() in ("1", "true"):
        return "已禁用（无浏览器模式）"
    path = configured.get(BROWSER_PATH_ENV, "")
    if path:
        exists = Path(path).expanduser().is_file()
        return f"外部浏览器：{path}" + ("" if exists else "（路径不存在，将降级）")
    channel = configured.get(BROWSER_CHANNEL_ENV, "")
    if channel:
        return f"浏览器通道：{channel}"
    has_chromium, has_headless = _managed_playwright_browser()
    if has_headless:
        return "Playwright 自带 Chromium（含 headless shell）"
    if has_chromium:
        return "Playwright 自带 Chromium（缺 headless shell，会改用系统浏览器或降级）"
    return "未找到（将尝试系统浏览器；都没有则降级）"


CN_SCRIPTS = resolve_cn_scripts()
# 默认输出目录：按 HOME 定位，换电脑自动跟随用户名，不写死个人路径。
DEFAULT_OUT = Path(
    os.environ.get("PLATFORM_SEARCH_OUT")
    or HOME / "Documents" / "platform-search"
)

# 每个平台的条数硬上限（用户要求：最多 50 条）。
HARD_CAP = 50
# 海外版引擎的 --days 上限。平台 API 本身也拿不到几十年前的数据，
# 传过大的天数只会拖慢检索，这里折中为 10 年，仍远大于 30 天。
GLOBAL_ENGINE_MAX_DAYS = 3650

# 平台注册表：id -> 中文标签。分组名在 GROUPS 中展开。
CN_LABELS = {
    "weibo": "微博",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "zhihu": "知乎",
    "douyin": "抖音",
    "wechat": "微信公众号",
    "baidu": "百度",
    "toutiao": "今日头条",
}
NATIVE_LABELS = {
    "hackernews": "Hacker News",
    "github": "GitHub",
    "reddit": "Reddit",
}
GLOBAL_LABELS = {
    "x": "X (Twitter)",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
}
# 上游引擎在多源组合 + 多词主题下常把这两个源裁成 0 条。默认拆成单独子批，
# 结果仍合并进同一份报告，用户不需要自己分批；--no-auto-split 可关闭。
SPLIT_GLOBAL_IDS = {"tiktok", "instagram"}
ALL_LABELS = {**CN_LABELS, **NATIVE_LABELS, **GLOBAL_LABELS}
GROUPS = {
    "cn": list(CN_LABELS),
    "native": list(NATIVE_LABELS),
    "global": list(GLOBAL_LABELS),
    "global-engine": list(GLOBAL_LABELS),
    "all": list(ALL_LABELS),
}

ENGAGEMENT_KEYS = (
    ("likes", "赞"), ("score", "分"), ("views", "浏览"), ("plays", "播放"),
    ("comments", "评论"), ("num_comments", "评论"), ("reposts", "转发"),
    ("collects", "收藏"), ("stars", "星"), ("reads", "阅读"),
)


# ---------------------------------------------------------------------------
# 参数与基础工具
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="跨平台检索（不限 30 天），按平台分组、组内按时间排序、每平台最多 50 条",
    )
    p.add_argument("topic", nargs="*", help="检索关键词")
    p.add_argument("--platforms", default="cn,native",
                   help="逗号分隔的平台 id 或分组 cn/native/global/all（默认 cn,native）")
    p.add_argument("--exclude", default="", metavar="LIST",
                   help="排除平台（逗号分隔），适合没有账号或不想检索的平台，如 --exclude weibo")
    p.add_argument("--global-platforms", default="", metavar="LIST",
                   help="自定义海外版引擎的源 id 列表（逗号分隔），会自动启用该引擎；"
                        "免 Key 可用: polymarket,devto,reddit,hackernews,github,grounding")
    p.add_argument("--no-auto-split", action="store_true",
                   help="关闭 TikTok/Instagram 自动单独子批；默认自动拆分并把结果合并成一份报告")
    p.add_argument("--since", default="2018-01-01", metavar="YYYY-MM-DD",
                   help="起始日期，默认 2018-01-01（不受 30 天限制；可用 --since 覆盖）")
    p.add_argument("--until", default=None, metavar="YYYY-MM-DD", help="截止日期，默认今天")
    p.add_argument("--per-platform", type=int, default=HARD_CAP, metavar="N",
                   help=f"每平台最多保留条数，默认 {HARD_CAP}，硬上限 {HARD_CAP}")
    p.add_argument("--sort", choices=("newest", "oldest"), default="newest",
                   help="组内时间排序方向，默认 newest（最新优先）")
    p.add_argument("--time-bucket", choices=("none", "year", "month"), default="none",
                   help="平台内再按年/月分小节，默认不分")
    p.add_argument("--global-query", default=None, metavar="Q",
                   help="海外平台使用的检索词（中文主题时建议给英文词）")
    p.add_argument("--emit", choices=("md", "json", "html", "compact", "path"), default="md",
                   help="输出模式，默认 md；除 path 外三种文件都会写入 --out 目录")
    p.add_argument("--out", default=None, metavar="DIR", help="输出目录，默认按 Path.home() 定位到用户 Documents/platform-search")
    p.add_argument("--timeout", type=int, default=900, metavar="SECS", help="整次运行的硬超时（默认 900s）")
    p.add_argument("--global-timeout", type=int, default=240, metavar="SECS",
                   help="海外模块子进程超时（默认 240s；单源可能需要较长时间）")
    p.add_argument("--cn-source-timeout", type=int, default=75, metavar="SECS",
                   help="国内模块单平台超时（默认 75s）")
    p.add_argument("--web-engines", default="default", metavar="LIST",
                   help="公开搜索引擎 id 或分组 cn/global/all/default（默认 6 个：4 个国内 + 2 个国际）；none 关闭")
    p.add_argument("--web-vertical", default="", metavar="SITE",
                   help="给搜索引擎查询追加 site: 限定，如 zhihu.com / github.com")
    p.add_argument("--web-limit", type=int, default=20, metavar="N",
                   help="每个搜索引擎最多保留条数（默认 20，硬上限 50）")
    p.add_argument("--web-delay", type=float, default=1.5, metavar="SECS",
                   help="搜索引擎请求间隔秒数（默认 1.5s）")
    p.add_argument("--web-timeout", type=int, default=10, metavar="SECS",
                   help="单个搜索引擎请求超时（默认 10s）")
    p.add_argument("--config-dir", default=None, metavar="DIR",
                   help="用户级配置目录（默认 Path.home() / .config / platform-search；也可用 PLATFORM_SEARCH_CONFIG_DIR）")
    p.add_argument("--show-config", action="store_true",
                   help="只显示配置来源与凭据文件是否就绪，不联网、不显示值")
    p.add_argument("--dry-run", action="store_true", help="只打印检索计划，不联网、不写文件")
    p.add_argument("--quiet", action="store_true", help="隐藏引擎的逐平台进度日志")
    return p


def resolve_platforms(spec: str):
    """把 --platforms 展开为 (cn 引擎平台 id 列表, 海外版引擎平台 id 列表)。"""
    cn_ids, global_ids = [], []
    for raw in (spec or "").split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in GROUPS:
            ids = GROUPS[token]
        elif token in ALL_LABELS:
            ids = [token]
        else:
            raise ValueError(f"未知平台 '{token}'。可用: {', '.join(ALL_LABELS)} 或分组 cn/native/global/all")
        for pid in ids:
            if pid in CN_LABELS or pid in NATIVE_LABELS:
                if pid not in cn_ids:
                    cn_ids.append(pid)
            elif pid not in global_ids:
                global_ids.append(pid)
    if not cn_ids and not global_ids:
        raise ValueError("--platforms 展开后没有任何平台")
    return cn_ids, global_ids


def resolve_dates(since: str, until: str):
    """校验并返回 (since, until) 的 YYYY-MM-DD 字符串。"""
    try:
        date.fromisoformat(since)
    except (TypeError, ValueError):
        raise ValueError(f"--since 日期无效: {since!r}（应为 YYYY-MM-DD）")
    until = until or date.today().isoformat()
    try:
        date.fromisoformat(until)
    except (TypeError, ValueError):
        raise ValueError(f"--until 日期无效: {until!r}（应为 YYYY-MM-DD）")
    if since > until:
        raise ValueError(f"--since ({since}) 晚于 --until ({until})")
    return since, until


def _read_env_file(path: Path) -> dict:
    """读取极简 KEY=VALUE 文件；只用于把用户级配置导出到进程环境，不打印任何值。"""
    out = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                out[key] = value
    except OSError:
        pass
    return out


def apply_user_config(explicit=None) -> dict:
    """把两个内置引擎指向用户级 platform-search 配置目录（可被环境变量覆盖）。

    该目录只有一份 .env（内置检索模块共用）与 browser_cookies/；按 Path.home()
    定位，新电脑上自动解析成新用户名下的 .config，不含任何写死的个人路径。
    优先级：进程环境变量 > 用户级 .env > 内置模块默认配置。
    必须在加载内置模块或启动子进程之前调用。
    """
    root = Path(
        explicit or os.environ.get("PLATFORM_SEARCH_CONFIG_DIR") or DEFAULT_CONFIG_ROOT
    ).expanduser().resolve()
    env_file = root / ".env"
    cookie_dir = root / "browser_cookies"
    markers = sorted(
        f.name[:-len("_login.json")] for f in cookie_dir.glob("*_login.json")
    ) if cookie_dir.is_dir() else []

    info = {
        "root": str(root),
        "env_file": env_file,
        "cookie_dir": str(cookie_dir),
        "env_keys": [],
        "cookies": len(list(cookie_dir.glob("*.json"))) if cookie_dir.is_dir() else 0,
        "logged_in": markers,
        "mode": "user-default",
    }

    if os.environ.get("PLATFORM_SEARCH_NO_CONFIG") == "1":
        info["mode"] = "disabled"
        return info

    # 把用户级 .env 导出到进程环境：所有内置检索模块共享本配置。
    if env_file.is_file():
        values = _read_env_file(env_file)
        aliases = {
            "PLATFORM_SEARCH_AUTH_TOKEN": "AUTH_TOKEN",
            "PLATFORM_SEARCH_CT0": "CT0",
            "PLATFORM_SEARCH_SCRAPECREATORS_API_KEY": "SCRAPECREATORS_API_KEY",
            "PLATFORM_SEARCH_BROWSER_CONSENT": "BROWSER_CONSENT",
            "PLATFORM_SEARCH_SETUP_COMPLETE": "SETUP_COMPLETE",
        }
        for key, value in values.items():
            if value and not os.environ.get(key):
                os.environ[key] = value
            legacy_key = aliases.get(key)
            if legacy_key and value and not os.environ.get(legacy_key):
                os.environ[legacy_key] = value
        info["env_keys"] = sorted(values)
        info["mode"] = "user-config"

    # 所有内置模块始终接收同一个配置根；即使 .env 尚不存在，也不能回退到
    # 各模块各自的隐藏默认目录。命令行环境变量仍具有最高优先级。
    os.environ.setdefault("PLATFORM_SEARCH_CONFIG_ROOT", str(root))
    os.environ.setdefault("PLATFORM_SEARCH_CONFIG_DIR", str(root))
    os.environ.setdefault("PLATFORM_SEARCH_OUTPUT_DIR", str(HOME / "Documents" / "platform-search"))
    os.environ.setdefault("PLATFORM_SEARCH_MEMORY_DIR", str(HOME / "Documents" / "platform-search" / "memory"))
    return info


def print_config_status(info: dict) -> None:
    """显示配置来源与就绪状态，只列文件名/键名，绝不显示凭据值。"""
    labels = {
        "user-config": "用户级 platform-search 配置（内置模块共用）",
        "custom": "外部自定义配置目录",
        "user-default": "未找到 platform-search 配置，使用内置模块默认配置",
        "disabled": "已禁用（PLATFORM_SEARCH_NO_CONFIG=1，不加载用户级配置）",
    }
    env_file = info["env_file"]
    keys = sorted(_read_env_file(env_file)) if env_file.is_file() else []
    env_desc = (f"存在（{env_file.stat().st_size} B；键：{', '.join(keys) or '无'}）"
                if keys or env_file.is_file() else "缺失")

    print(f"配置目录   : {info['root']}")
    print(f"定位方式   : Path.home()/.config/platform-search（换电脑自动跟随用户名）")
    print(f"生效模式   : {labels.get(info['mode'], info['mode'])}")
    print(f"  .env             : {env_desc}")
    print(f"  browser_cookies  : {info['cookies']} 个 json；已登录：{', '.join(info['logged_in']) or '无'}")
    print(f"  PLATFORM_SEARCH_CONFIG_ROOT = {info.get('config_root') or info['root']}")
    print("优先级：进程环境变量 > 当前用户级 platform-search/.env > 内置模块默认配置")
    print(f"浏览器     : {browser_mode_label()}")


def find_global_script():
    """返回项目内置的海外检索模块运行入口。"""
    runner = ENGINE_DIR / "global" / "runner.py"
    return runner if runner.is_file() else None


def norm_date(value) -> str:
    """把各种日期写法归一化为 YYYY-MM-DD；无法识别返回空串。"""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if re.fullmatch(r"\d{10,13}", s):  # Unix 时间戳（秒/毫秒）
        try:
            ts = int(s[:10])
            return datetime.fromtimestamp(ts).date().isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    today = date.today()
    if any(word in s for word in ("刚刚", "分钟前", "小时前", "今天", "今天发布")):
        return today.isoformat()
    if "昨天" in s:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*个月前", s)
    if m:
        return (today - timedelta(days=30 * int(m.group(1)))).isoformat()
    return ""


def engagement_text(eng) -> str:
    """把互动数据字典压成一段短文本。"""
    if not isinstance(eng, dict):
        return ""
    parts = []
    for key, label in ENGAGEMENT_KEYS:
        v = eng.get(key)
        if isinstance(v, (int, float)) and v and not isinstance(v, bool):
            parts.append(f"{label} {int(v):,}")
        if len(parts) >= 3:
            break
    return " · ".join(parts)


def make_row(raw: dict, default_platform: str):
    """把上游任意平台的原始条目归一化为统一结构。"""
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or raw.get("text") or raw.get("snippet") or raw.get("body") or "").strip()
    title = re.sub(r"\s+", " ", title)[:220]
    url = str(raw.get("url") or "").strip()
    if not title and not url:
        return None
    platform = str(raw.get("platform") or "").strip().lower()
    source = str(raw.get("source") or "")
    if not platform and source.startswith("upstream:"):
        platform = source.split(":", 1)[1].strip().lower()
    platform = platform or default_platform
    author = str(raw.get("author") or raw.get("author_handle") or raw.get("author_name") or "").strip()
    return {
        "platform": platform,
        "title": title or url,
        "url": url,
        "date": norm_date(raw.get("date") or raw.get("published_at")),
        "author": author,
        "engagement": engagement_text(raw.get("engagement")),
        "snippet": re.sub(r"\s+", " ", str(raw.get("snippet") or raw.get("text") or ""))[:300],
    }


def dedupe_rows(rows):
    """按 URL（无 URL 时按标题+日期）去重，保留首次出现。"""
    seen, out = set(), []
    for r in rows:
        key = r["url"] or f"{r['platform']}|{r['title']}|{r['date']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _engagement_value(text: str) -> float:
    """从 '浏览 46,206,851' / '点赞 1.2万' 里取数值，取不到返回 -1。

    YouTube 走 flat 搜索拿不到上传日期，条目全无日期；若不做这一步，
    无日期条目会保持上游 rerank 顺序（实测 125K 播放量排在 1.2M 前面）。
    """
    if not text:
        return -1.0
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*万", text)
    if m:
        return float(m.group(1).replace(",", "")) * 10000
    m = re.search(r"([\d,]+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return -1.0


def filter_date_window(rows, since, until):
    """按 --since/--until 硬过滤有日期的条目，返回 (保留行, 剔除数)。

    cn 引擎自身不按 since 过滤：实测传 --since 2018-01-01，公众号 / 今日头条 /
    知乎仍返回 2016~2017 年的内容（11 条越界），所以在汇总层再兜一次。
    无日期条目无法判断时间，保留。
    """
    if not since and not until:
        return rows, 0
    kept = []
    for r in rows:
        d = r.get("date")
        if d and since and d < since:
            continue
        if d and until and d > until:
            continue
        kept.append(r)
    return kept, len(rows) - len(kept)


def sort_and_cap(rows, order: str, cap: int):
    """有日期的按时间排序；无日期的排在最后，其中带互动量的按热度降序。

    搜索引擎条目没有互动量（值为 -1），稳定排序下保持引擎自己的相关度顺序。
    """
    dated = [r for r in rows if r["date"]]
    undated = [r for r in rows if not r["date"]]
    dated.sort(key=lambda r: r["date"], reverse=(order == "newest"))
    undated.sort(key=lambda r: _engagement_value(r.get("engagement") or ""), reverse=True)
    return (dated + undated)[:cap]


def slugify(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", topic.lower()).strip("-")[:40]
    return slug or "search"


# ---------------------------------------------------------------------------
# 内置检索模块
# ---------------------------------------------------------------------------

def run_cn_engine(topic, since, until, cn_ids, global_query, source_timeout, quiet):
    """调用内置国内检索模块，传很宽的日期窗口以绕开 30 天限制。"""
    if not cn_ids:
        return {}, {}
    if not (CN_SCRIPTS / "lib" / "pipeline.py").is_file():
        return {}, {"cn": f"未找到内置抓取引擎: {CN_SCRIPTS}"}

    sys.path.insert(0, str(CN_SCRIPTS))
    from lib import pipeline, env as cn_env, query as cn_query  # noqa: E402

    # get_config() 会加载 .env 并把 PLATFORM_SEARCH_BROWSER_PATH 等运行时开关导出，
    # 因此国内平台的登录态、浏览器路径与用户级配置完全一致。
    config = cn_env.get_config()
    ctx = pipeline.RunContext(
        topic=topic,
        search_topic=cn_query.search_keyword(topic),
        from_date=since,
        to_date=until,
        depth="deep",
        config=config,
        overseas_query=global_query or topic,
        upstream_platforms=(),
        upstream_timeout=source_timeout,
    )
    # 逐平台硬超时：deep profile 默认到 150s，这里统一压到 --cn-source-timeout。
    timeouts = {
        k: min(int(v), source_timeout)
        for k, v in pipeline.TIMEOUT_PROFILES["default"].items()
        if isinstance(v, (int, float))
    }
    raw = pipeline.run_sources(ctx, cn_ids, timeouts=timeouts, log=not quiet)

    rows_by_platform, errors = {}, {}
    for sid, res in raw.items():
        rows = [make_row(r, sid) for r in (res.get("items") or [])]
        rows_by_platform[sid] = [r for r in rows if r]
        if res.get("error"):
            errors[sid] = str(res["error"])
    return rows_by_platform, errors


def _extract_json(stdout: str):
    text = (stdout or "").strip()
    if not text:
        raise ValueError("内置海外检索模块没有输出 JSON")
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _youtube_topic_guard(rows, topic):
    """剔除标题完全不含主题词的 YouTube 条目。

    flat 搜索拿不到上传日期，条目会按播放量排序，于是 YouTube 把「女团」
    当检索词带出来的无关大播放量视频（如 46M 的 tripleS MV、小说短片）
    会顶到第一位。这里只做最朴素的「标题/摘要是否命中主题词」判断，
    不用上游 relevance——它对韩文/中文标题一律给 0，会把最相关的直拍误杀。
    命中不足时整体放弃过滤，宁可留噪声也不清空结果。
    """
    if len(rows) < 10:
        return rows
    toks = [w for w in re.findall(r"[a-z]{3,}", (topic or "").lower())]
    if not toks:
        return rows

    def hit(row):
        blob = f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()
        blob = re.sub(r"[\s\-_·.,'\"|/()\[\]]+", "", blob)
        return all(w in blob for w in toks)

    kept = [r for r in rows if hit(r)]
    return kept if len(kept) >= max(5, len(rows) // 3) else rows


def run_global_engine(topic, global_query, since, until, global_ids, per_platform, timeout, quiet):
    """调用内置海外检索模块 CLI，按 items_by_source 分平台收集。"""
    if not global_ids:
        return {}, {}
    script = find_global_script()
    if not script:
        return {}, {"global": "未找到项目内置的海外检索模块"}

    days = max(1, (date.fromisoformat(until) - date.fromisoformat(since)).days)
    days = min(days, GLOBAL_ENGINE_MAX_DAYS)
    cmd = [
        sys.executable, str(script), global_query or topic,
        "--emit", "json", "--json-profile", "raw",
        "--days", str(days), "--as-of", until,
        "--max-per-source", str(per_platform),
        "--max-results", str(per_platform * max(1, len(global_ids))),
        "--search", ",".join(global_ids),
        # 内置海外模块默认会做规划/时效校验，单源常要 80s+；关掉非必要环节。
        "--quick", "--no-verify-freshness",
    ]
    if not quiet:
        sys.stderr.write(f"[海外检索模块] 检索 {','.join(global_ids)}（回溯 {days} 天，超时 {timeout}s）\n")
    try:
        # 内置海外模块默认按 quick 档限量（YouTube 6 / X 12 / TikTok 10 / IG 10），
        # 统一用「每平台上限」覆盖，避免「YouTube 只有 4 条 / X 只有 8 条」这类
        # 看起来像抓取失败、实际是被档位截断的结果。
        env = dict(
            os.environ,
            PLATFORM_SEARCH_YOUTUBE_SEARCH_COUNT=str(per_platform),
            PLATFORM_SEARCH_X_SEARCH_COUNT=str(per_platform),
            PLATFORM_SEARCH_TIKTOK_COUNT=str(per_platform),
            PLATFORM_SEARCH_INSTAGRAM_COUNT=str(per_platform),
        )
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {}, {"global": f"内置海外检索模块超时（{timeout}s），已跳过"}
    except OSError as exc:
        return {}, {"global": f"内置海外检索模块启动失败: {exc}"}

    try:
        payload = _extract_json(proc.stdout)
    except ValueError as exc:
        # 引擎把「哪些源跑不了、需要什么 Key」写在 stderr 末尾，倒序拼接让
        # 关键结论排在前面，平台级错误截断后仍能看到根因。
        tail = (proc.stderr or "").strip().splitlines()[-4:]
        detail = " | ".join(reversed(tail))
        return {}, {"global": f"内置海外检索模块未返回 JSON（退出码 {proc.returncode}，{exc}）：{detail}"}

    by_source = payload.get("items_by_source") or {}
    rows_by_platform = {}
    if isinstance(by_source, dict):
        for platform, rows in by_source.items():
            key = str(platform).strip().lower()
            collected = [make_row(r, key) for r in (rows or []) if isinstance(r, dict)]
            rows_by_platform.setdefault(key, []).extend(r for r in collected if r)
    if "youtube" in rows_by_platform:
        before = len(rows_by_platform["youtube"])
        rows_by_platform["youtube"] = _youtube_topic_guard(
            rows_by_platform["youtube"], global_query or topic
        )
        if not quiet and len(rows_by_platform["youtube"]) != before:
            sys.stderr.write(
                f"[YouTube] 主题词兜底过滤：{before} → {len(rows_by_platform['youtube'])} 条\n"
            )
    errors = {}
    for key, value in (payload.get("errors_by_source") or {}).items():
        if value:
            errors[str(key)] = str(value)
    if proc.returncode != 0 and not rows_by_platform and not errors:
        errors["global"] = f"内置海外检索模块退出码 {proc.returncode}"
    return rows_by_platform, errors


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def build_payload(args, topic, since, until, cap, cn_ids, global_ids,
                  platform_rows, raw_counts, errors, script_path, config_info=None,
                  date_filtered=0):
    # 读取登录标记，只读布尔状态、不接触 cookie 内容。COOKIE_DIR 由
    # apply_user_config() 指向 ~/.config/platform-search 或用户显式指定的目录。
    try:
        sys.path.insert(0, str(CN_SCRIPTS))
        from lib import crawler_bridge
        login_state = {
            pid: bool(crawler_bridge.has_login(pid))
            for pid in ("weibo", "xiaohongshu", "zhihu", "douyin", "bilibili")
        }
    except Exception:
        login_state = {}
    platforms = []
    for pid in cn_ids + global_ids:
        rows = platform_rows.get(pid, [])
        dates = sorted(r["date"] for r in rows if r["date"])
        if pid in CN_LABELS or pid in NATIVE_LABELS:
            engine = "platform-search"
        elif pid.startswith("web:"):
            engine = "platform-search-web"
        else:
            engine = "platform-search"
        err = errors.get(pid)
        if not err:
            # 模块级故障（找不到模块、子进程失败、输出无法解析）要落到每个平台上，
            # 否则报告会把「引擎没跑起来」误显示成「该平台没有结果」。
            engine_key = ("cn" if engine == "platform-search"
                          else "web" if pid.startswith("web:") else "global")
            if errors.get(engine_key):
                err = f"引擎级故障：{str(errors[engine_key])[:260]}"
        platforms.append({
            "id": pid,
            "label": (ALL_LABELS.get(pid)
                      or web_engines.ENGINE_LABELS.get(pid[4:] if pid.startswith("web:") else pid, pid)),
            "engine": engine,
            "logged_in": login_state.get(pid),
            "count": len(rows),
            "raw_count": raw_counts.get(pid, 0),
            "date_from": dates[0] if dates else None,
            "date_to": dates[-1] if dates else None,
            "error": err,
            "items": rows,
        })
    return {
        "topic": topic,
        "since": since,
        "until": until,
        "per_platform": cap,
        "sort": args.sort,
        "time_bucket": args.time_bucket,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "engines": {
            "domestic": str(CN_SCRIPTS),
            "global": str(script_path) if script_path else None,
        },
        # 所有内置检索模块共用同一个用户级目录。
        "config": {
            "root": (config_info or {}).get("root"),
            "source": (config_info or {}).get("mode"),
            "config_dir": (config_info or {}).get("root"),
            "engine_config_dir": (config_info or {}).get("root"),
        },
        "platform_count": len(platforms),
        "total": sum(p["count"] for p in platforms),
        "date_filtered": date_filtered,
        "platforms": platforms,
        "errors": errors,
    }


def _item_line(idx: int, row: dict) -> str:
    head = row["date"] or "日期未知"
    title = f"[{row['title']}]({row['url']})" if row["url"] else row["title"]
    bits = [head, title]
    if row["author"]:
        bits.append(row["author"])
    if row["engagement"]:
        bits.append(row["engagement"])
    return f"{idx}. " + " | ".join(bits)


def render_md(payload) -> str:
    p = payload
    lines = [
        f"# 跨平台检索：{p['topic']}",
        "",
        f"- 时间范围：{p['since']} ~ {p['until']}（不按 30 天截断）",
        f"- 排序：{'最新优先' if p['sort'] == 'newest' else '最早优先'}；每平台上限：{p['per_platform']} 条",
        f"- 平台数：{p['platform_count']}；结果总数：{p['total']} 条",
        f"- 生成时间：{p['generated_at']}",
        "",
    ]
    if p.get("date_filtered"):
        lines.insert(5, f"- 时间过滤：已剔除 {p['date_filtered']} 条超出该范围的旧/新结果"
                        f"（内置来源模块自身不统一按 since 过滤）")
    for platform in p["platforms"]:
        rng = f"{platform['date_from']} ~ {platform['date_to']}" if platform["date_from"] else "无可识别日期"
        lines.append(f"## {platform['label']}（{platform['count']} 条 / {rng}）")
        lines.append("")
        state = platform.get("logged_in")
        if state is True:
            login_text = "已登录（复用用户级登录态）"
        elif state is False:
            login_text = "未登录（走公开/热搜兜底）"
        else:
            login_text = "免登录"
        lines.append(f"引擎：{platform['engine']}；登录态：{login_text}；"
                     f"原始 {platform['raw_count']} 条，去重排序后保留 {platform['count']} 条。")
        if platform["error"]:
            lines.append(f"检索异常：{platform['error']}")
        lines.append("")
        if not platform["items"]:
            lines.append("（无结果）")
            lines.append("")
            continue

        bucket = p["time_bucket"]
        if bucket == "none":
            groups = [("", platform["items"])]
        else:
            grouped = {}
            for row in platform["items"]:
                key = (row["date"][:4] if bucket == "year" else row["date"][:7]) if row["date"] else "日期未知"
                grouped.setdefault(key, []).append(row)
            groups = sorted(grouped.items(), key=lambda kv: kv[0], reverse=(p["sort"] == "newest"))
            # 日期未知永远放最后
            groups.sort(key=lambda kv: kv[0] == "日期未知")

        idx = 0
        for label, rows in groups:
            if label:
                lines.append(f"### {label}（{len(rows)} 条）")
                lines.append("")
            for row in rows:
                idx += 1
                lines.append(_item_line(idx, row))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_compact(payload) -> str:
    p = payload
    lines = [
        f"跨平台检索：{p['topic']} | {p['since']} ~ {p['until']} | 共 {p['total']} 条 / {p['platform_count']} 个平台",
    ]
    if p.get("date_filtered"):
        lines.append(f"  时间过滤：已剔除 {p['date_filtered']} 条超出该范围的结果")
    for platform in p["platforms"]:
        rng = f"{platform['date_from']} ~ {platform['date_to']}" if platform["date_from"] else "日期未知"
        state = platform.get("logged_in")
        login = "已登录" if state is True else ("未登录·公开兜底" if state is False else "免登录")
        flag = f" [异常: {platform['error']}]" if platform["error"] else ""
        lines.append(f"  {platform['label']}: {platform['count']} 条（{rng}）[{login}]{flag}")
    return "\n".join(lines) + "\n"


def render_html(payload) -> str:
    p = payload
    esc = html_lib.escape
    parts = [
        "<!DOCTYPE html>", '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>跨平台检索 - {esc(p['topic'])}</title>",
        "<style>",
        ":root{color-scheme:light dark}",
        "body{font-family:system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;",
        "max-width:960px;margin:32px auto;padding:0 20px;line-height:1.65;background:#fff;color:#1a1a1a}",
        "@media(prefers-color-scheme:dark){body{background:#14161a;color:#e8e8e8}a{color:#4ea1ff}}",
        "a{color:#0969da}h2{border-bottom:1px solid #8884;padding-bottom:6px;margin-top:36px}",
        "li{margin:6px 0}.meta{color:#777;font-size:.9em}.warn{color:#c0392b}",
        "code{background:#8882;padding:2px 5px;border-radius:4px}",
        "</style></head><body>",
        f"<h1>跨平台检索：{esc(p['topic'])}</h1>",
        "<p class=\"meta\">"
        f"时间范围 {esc(p['since'])} ~ {esc(p['until'])}（不按 30 天截断） · "
        f"{'最新优先' if p['sort'] == 'newest' else '最早优先'} · "
        f"每平台上限 {p['per_platform']} 条 · 共 {p['total']} 条 / {p['platform_count']} 个平台 · "
        f"生成于 {esc(p['generated_at'])}</p>",
    ]
    if p.get("date_filtered"):
        parts.append(
            f'<p class="warn">时间过滤：已剔除 {p["date_filtered"]} 条超出 '
            f'{esc(p["since"])} ~ {esc(p["until"])} 的结果（内置来源模块自身不统一按 since 过滤）</p>'
        )
    for platform in p["platforms"]:
        rng = f"{platform['date_from']} ~ {platform['date_to']}" if platform["date_from"] else "无可识别日期"
        parts.append(f"<h2>{esc(platform['label'])}（{platform['count']} 条）</h2>")
        state = platform.get("logged_in")
        login_text = ("已登录（复用用户级登录态）" if state is True
                      else "未登录（公开/热搜兜底）" if state is False else "免登录")
        parts.append(f"<p class=\"meta\">{esc(rng)} · 引擎 {esc(platform['engine'])} · 登录态 {esc(login_text)} · "
                     f"原始 {platform['raw_count']} 条，保留 {platform['count']} 条</p>")
        if platform["error"]:
            parts.append(f"<p class=\"warn\">检索异常：{esc(str(platform['error']))}</p>")
        if not platform["items"]:
            parts.append("<p>（无结果）</p>")
            continue
        parts.append("<ol>")
        for row in platform["items"]:
            title = f'<a href="{esc(row["url"])}" target="_blank" rel="noopener">{esc(row["title"])}</a>' if row["url"] else esc(row["title"])
            bits = [f"<code>{esc(row['date'] or '日期未知')}</code>", title]
            if row["author"]:
                bits.append(esc(row["author"]))
            if row["engagement"]:
                bits.append(esc(row["engagement"]))
            parts.append("<li>" + " | ".join(bits) + "</li>")
        parts.append("</ol>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args()
    cfg = apply_user_config(args.config_dir)
    if args.show_config:
        print_config_status(cfg)
        return 0
    topic = " ".join(args.topic or []).strip()
    if not topic:
        print("错误：请提供检索关键词。", file=sys.stderr)
        return 2

    try:
        since, until = resolve_dates(args.since, args.until)
        cn_ids, global_ids = resolve_platforms(args.platforms)
        web_ids = ([] if args.web_engines.strip().lower() == "none"
                   else web_engines.resolve_engines(args.web_engines))
        if args.global_platforms:
            extra = [t.strip().lower() for t in args.global_platforms.split(",") if t.strip()]
            if not extra:
                raise ValueError("--global-platforms 需要至少一个海外源 id")
            # 显式指定海外源就自动启用海外版引擎，不必再写 --platforms all。
            global_ids = list(dict.fromkeys(extra))
        if args.exclude:
            excluded = {t.strip().lower() for t in args.exclude.split(",") if t.strip()}
            unknown = excluded - (set(ALL_LABELS) | set(web_engines.ENGINE_SPECS))
            if unknown:
                raise ValueError(f"--exclude 含未知平台: {', '.join(sorted(unknown))}")
            cn_ids = [x for x in cn_ids if x not in excluded]
            global_ids = [x for x in global_ids if x not in excluded]
            web_ids = [x for x in web_ids if x not in excluded]
            if not cn_ids and not global_ids and not web_ids:
                raise ValueError("--exclude 排除后没有任何源可检索")
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    cap = max(1, min(args.per_platform, HARD_CAP))
    if args.per_platform > HARD_CAP:
        print(f"提示：每平台上限硬限制为 {HARD_CAP} 条，--per-platform={args.per_platform} 已按 {HARD_CAP} 处理。",
              file=sys.stderr)

    # 需要在浏览器里取数的源：国内引擎的登录态平台 + Google 搜索层。
    # dry-run 也做一次探测，只改进程内环境变量、不联网、不写文件，用于显示真实计划。
    if cn_ids or "google" in web_ids:
        ensure_browser_config(quiet=args.quiet or args.dry_run)

    # 重新读一次环境变量：允许技能级 config/.env 覆盖输出目录
    # （DEFAULT_OUT 在模块加载时算好，那时 apply_user_config() 还没跑）。
    # 一次调用只产出一份报告：把易被多源组合裁剪的源拆成单独子批，结果合并回来。
    # 放在 --exclude 之后计算，确保被排除的源不会又被自动分批拉回来。
    auto_split = not args.no_auto_split
    split_ids = [x for x in global_ids if x in SPLIT_GLOBAL_IDS] if auto_split else []
    main_global_ids = [x for x in global_ids if x not in split_ids]

    out_dir = (Path(args.out).expanduser() if args.out else Path(
        os.environ.get("PLATFORM_SEARCH_OUT") or DEFAULT_OUT
    ).expanduser())
    script_path = find_global_script() if global_ids else None

    if args.dry_run:
        print("检索计划（dry-run，不联网、不写文件）")
        print(f"  关键词        : {topic}")
        print(f"  时间范围      : {since} ~ {until}（不按 30 天截断）")
        print(f"  排序 / 上限   : {args.sort} / 每平台 {cap} 条")
        print(f"  时间分小节    : {args.time_bucket}")
        print(f"  国内模块平台   : {', '.join(cn_ids) or '（无）'}")
        print(f"  海外模块平台  : {', '.join(main_global_ids) or '（无）'}")
        print(f"  自动单独子批  : {', '.join(split_ids) or '（无）'}（结果合并进同一份报告）")
        print(f"  海外模块入口  : {script_path or '（未启用）'}")
        print(f"  公开搜索引擎  : {', '.join(web_ids) or '（未启用）'}")
        print(f"  浏览器        : {browser_mode_label()}")
        print(f"  搜索引擎垂直站: {args.web_vertical or '（无）'}")
        print(f"  输出目录      : {out_dir}")
        print(f"  配置目录      : {cfg['root']}（{cfg['mode']}）")
        return 0

    # 硬超时看门狗（Windows 没有 SIGALRM，用守护线程）。
    def _watchdog():
        sys.stderr.write(f"\n[超时] 全局超时 {args.timeout}s，已退出。\n")
        sys.stderr.flush()
        os._exit(124)

    timer = threading.Timer(args.timeout, _watchdog)
    timer.daemon = True
    timer.start()

    cn_rows, cn_errors = run_cn_engine(
        topic, since, until, cn_ids, args.global_query,
        args.cn_source_timeout, args.quiet,
    )
    global_rows, global_errors = run_global_engine(
        topic, args.global_query, since, until, main_global_ids,
        cap, args.global_timeout, args.quiet,
    )
    if split_ids:
        if not args.quiet:
            sys.stderr.write(f"[自动分批] 单独检索 {','.join(split_ids)}，结果合并进同一份报告\n")
        extra_rows, extra_errors = run_global_engine(
            topic, args.global_query, since, until, split_ids,
            cap, args.global_timeout, args.quiet,
        )
        for pid, rows in extra_rows.items():
            global_rows.setdefault(pid, []).extend(rows)
        for key, value in extra_errors.items():
            if key == "global":
                # 引擎级故障只落到子批源，避免误标到主批的所有海外平台。
                for pid in split_ids:
                    global_errors.setdefault(pid, f"单独子批失败：{value}")
            else:
                global_errors.setdefault(key, value)
    web_rows, web_errors = {}, {}
    if web_ids:
        web_rows, web_errors = web_engines.search_web(
            topic, web_ids, since, until, args.web_vertical,
            limit=args.web_limit, delay=args.web_delay, timeout=args.web_timeout,
            log=None if args.quiet else (lambda m: sys.stderr.write(m + "\n")),
        )
        # 加 web: 前缀，避免与平台 id（baidu / github 等）冲突
        web_rows = {f"web:{k}": v for k, v in web_rows.items()}
        web_errors = {f"web:{k}": v for k, v in web_errors.items()}
    web_pids = [f"web:{k}" for k in web_ids]

    platform_rows, raw_counts = {}, {}
    for source_map in (cn_rows, global_rows, web_rows):
        for pid, rows in source_map.items():
            platform_rows.setdefault(pid, []).extend(rows)
            raw_counts[pid] = raw_counts.get(pid, 0) + len(rows)
    # 用户请求的平台即使 0 条也要出现在报告里，便于看清失败情况。
    for pid in cn_ids + global_ids + web_pids:
        platform_rows.setdefault(pid, [])

    errors = {**cn_errors, **global_errors, **web_errors}
    date_filtered = 0
    for pid, rows in platform_rows.items():
        rows, dropped = filter_date_window(rows, since, until)
        date_filtered += dropped
        platform_rows[pid] = sort_and_cap(dedupe_rows(rows), args.sort, cap)
    if date_filtered and not args.quiet:
        sys.stderr.write(f"[时间窗口] 剔除 {date_filtered} 条超出 {since} ~ {until} 的结果\n")

    payload = build_payload(
        args, topic, since, until, cap, cn_ids, global_ids + web_pids,
        platform_rows, raw_counts, errors, script_path, cfg,
        date_filtered=date_filtered,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"platforms_{slugify(topic)}"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    html_path = out_dir / f"{stem}.html"
    md_text = render_md(payload)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    html_text = render_html(payload)
    md_path.write_text(md_text, encoding="utf-8")
    json_path.write_text(json_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")

    if args.emit == "md":
        print(md_text, end="")
    elif args.emit == "json":
        print(json_text)
    elif args.emit == "html":
        print(html_text)
    elif args.emit == "compact":
        print(render_compact(payload), end="")
    elif args.emit == "path":
        print(md_path)
    sys.stderr.write(f"已保存: {md_path} / {json_path} / {html_path}\n")
    timer.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
