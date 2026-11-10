# platform-search-skill

`platform-search-skill` 是一个可单独复制、单独运行的跨平台互联网检索引擎。它不要求安装其他技能包，也不依赖固定宿主目录。

技能名称统一为 `platform-search-skill`；运行时入口脚本仍为 `scripts/platform_search.py`，配置目录与输出目录仍为 `~/.config/platform-search` 和 `~/Documents/platform-search`。

支持三类来源：

- 中文内容平台：微博、小红书、B站、知乎、抖音、微信公众号、百度、今日头条；
- 海外内容平台：X、YouTube、TikTok、Instagram、Hacker News、GitHub、Reddit；
- 公开搜索入口：必应中国、必应国际、360、搜狗、Google、DuckDuckGo。

## 当前版本

当前版本：**2.0.2**。本版本统一了项目元数据、Skill 版本号和独立分发包版本，默认配置、登录态、浏览器和输出目录继续使用统一的 `platform-search` 命名空间。

## 运行环境

统一要求 **Python 3.11+**。项目不再为不同来源维护不同 Python 基线。

可选运行时依赖按功能准备：

- Playwright + Chromium：浏览器模式和 Google；
- yt-dlp：YouTube；
- Node 与对应 CLI：部分 X 检索方式；
- 平台 Cookie / API Key：需要登录态或受限接口的平台。

## 快速运行

```bash
python scripts/platform_search.py "遥感"
python scripts/platform_search.py "遥感" --dry-run
python scripts/platform_search.py "遥感" --platforms all --web-engines all
python scripts/platform_search.py "遥感" --emit compact
python scripts/platform_search.py --show-config
```

默认结果写入 `~/Documents/platform-search/`，默认配置目录为 `~/.config/platform-search/`。也可以通过 `--config-dir`、`--out` 或对应的 `PLATFORM_SEARCH_*` 环境变量覆盖。浏览器可执行文件路径属于当前机器的本地覆盖项，不会写入分发包。

## 独立结构

```text
platform-search-skill/
├─ scripts/
│  ├─ platform_search.py       # 统一入口与编排
│  ├─ web_engines.py            # 公开搜索层
│  ├─ engine/
│  │  ├─ domestic/              # 中文来源适配器
│  │  └─ global/                # 海外来源适配器
│  ├─ sync_config.py             # 统一配置管理
│  ├─ sync_vendor.py             # 内置模块完整性检查
│  └─ package_filtered.py        # 干净打包
├─ pyproject.toml                # Python 3.11+ 唯一版本基线
├─ SKILL.md                      # 技能入口与版本元数据
└─ LICENSE                       # MIT 许可证
```

所有运行时入口、配置路径、环境变量和报告字段均使用 `platform-search` 自有命名。项目不会自动发现或读取其他软件的安装目录。

## 配置

在 `~/.config/platform-search/.env` 写入所需配置，例如：

```text
PLATFORM_SEARCH_BROWSER_PATH=C:/path/to/chrome.exe
PLATFORM_SEARCH_BROWSER_CHANNEL=chrome
PLATFORM_SEARCH_DISABLE_BROWSER=0
```

不要把 `.env`、Cookie 或 API Key 提交到仓库。登录 Cookie 直接放在 `~/.config/platform-search/browser_cookies/`。

## 参数摘要

| 参数 | 说明 |
|---|---|
| `--platforms` | `cn`、`native`、`global`、`all` 或具体平台 id |
| `--web-engines` | `default`、`cn`、`global`、`all`、`none` |
| `--since` / `--until` | 日期范围，默认从 2018 年开始 |
| `--per-platform` | 每个平台保留条数，硬上限 50 |
| `--global-query` | 为海外来源单独指定检索词 |
| `--emit` | `md`、`json`、`html`、`compact`、`path` |
| `--dry-run` | 只打印计划，不联网、不写报告 |
| `--show-config` | 查看配置状态，不显示凭据值 |

## 维护与打包

```bash
python scripts/sync_vendor.py --dry-run
python scripts/package_filtered.py --out-dir dist
```

源码升级通过本项目自己的版本管理完成；完整性检查脚本不会读取、覆盖或同步其他目录。

## 许可与合规

自有编排、公开搜索和重构代码采用 MIT 许可，详见根目录 `LICENSE`。内置适配器中包含基于 MIT 许可代码的保留实现，其来源、作者和修改范围保留在各适配器目录的许可证文件与源文件头注释中。

本项目仅用于学习、研究与个人知识整理。请遵守各平台服务条款和 `robots.txt`，控制请求频率，不进行大规模采集、个人数据收集或违法用途。
