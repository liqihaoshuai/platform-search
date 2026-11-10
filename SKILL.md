---
name: platform-search-skill
version: "2.0.2"
description: '独立跨平台互联网检索引擎：支持 21 个来源，默认启用 17 个来源；按来源分组输出 md/json/html，默认检索 2018 年至今，每来源最多 50 条。统一使用 Python 3.11+、~/.config/platform-search 配置和 PLATFORM_SEARCH_* 环境变量。'
argument-hint: '搜全网 AI 编程助手 | 全网检索 Claude Code --platforms all --global-query "Claude Code"'
allowed-tools: Bash, Read, Write
user-invocable: true
metadata:
  tags:
    - research
    - cross-platform
    - chinese-platforms
    - time-unbounded
---

# platform-search-skill

当前版本：**2.0.2**。

独立跨平台互联网检索引擎，支持 21 个来源：15 个内容平台和 6 个公开搜索入口。默认启用 17 个来源，结果按来源分组；无可靠日期的结果按互动量或相关度排序。默认时间范围为 2018 年至今，每来源最多 50 条，同时输出 Markdown、JSON 和 HTML。

## 1. 来源与运行

| 类别 | 来源 |
|---|---|
| 中文内容平台 | 微博、小红书、B站、知乎、抖音、微信公众号、百度、今日头条 |
| 海外内容平台 | X、YouTube、TikTok、Instagram、Hacker News、GitHub、Reddit |
| 公开搜索入口 | 必应中国、必应国际、360、搜狗、Google、DuckDuckGo |

统一运行环境：**Python 3.11+**。项目只使用自身 `scripts/engine/` 中的适配器，不自动发现、读取或启动其他软件的检索模块。

可选依赖：Playwright + Chromium、yt-dlp、Node/CLI，以及各平台所需的 Cookie 或 API Key。缺少某个平台的依赖时，报告会保留该平台并写明错误，不会伪装成“无人讨论”。

## 2. 安装与调用

将整个 `platform-search-skill/` 目录放入任意本地目录即可，不要求固定宿主路径。

```bash
python {{SKILL_DIR}}/scripts/platform_search.py "关键词"
python {{SKILL_DIR}}/scripts/platform_search.py "关键词" --dry-run
python {{SKILL_DIR}}/scripts/platform_search.py --show-config
python {{SKILL_DIR}}/scripts/platform_search.py "遥感" --platforms all --web-engines all
```

默认输出：`Path.home() / "Documents" / "platform-search"`

默认配置：`Path.home() / ".config" / "platform-search"`

## 3. 统一配置

配置文件为：

```text
~/.config/platform-search/.env
~/.config/platform-search/browser_cookies/
```

用户配置统一使用 `PLATFORM_SEARCH_*` 命名；内置适配器需要的兼容键由顶层入口在进程内映射，不需要用户再维护第二份配置。示例：

```text
PLATFORM_SEARCH_BROWSER_PATH=C:/path/to/chrome.exe
PLATFORM_SEARCH_BROWSER_CHANNEL=chrome
PLATFORM_SEARCH_DISABLE_BROWSER=0
```

配置目录可由 `--config-dir DIR` 或 `PLATFORM_SEARCH_CONFIG_DIR` 覆盖，输出目录可由 `--out DIR` 或 `PLATFORM_SEARCH_OUT` 覆盖。不要把 `.env`、Cookie 或 API Key 提交到仓库。

`sync_config.py` 只管理当前统一配置，不会扫描或同步其他软件的配置目录：

```bash
python {{SKILL_DIR}}/scripts/sync_config.py --dry-run
python {{SKILL_DIR}}/scripts/sync_config.py
```

## 4. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--platforms` | `cn,native` | `cn`、`native`、`global`、`all` 或具体平台 id |
| `--global-platforms` | 空 | 额外启用海外来源 id |
| `--web-engines` | `default` | `default`、`cn`、`global`、`all`、`none` |
| `--exclude` | 空 | 排除平台，逗号分隔 |
| `--since` | `2018-01-01` | 起始日期 |
| `--until` | 今天 | 截止日期 |
| `--per-platform` | `50` | 每来源保留条数，硬上限 50 |
| `--sort` | `newest` | `newest` 或 `oldest` |
| `--time-bucket` | `none` | `none`、`year`、`month` |
| `--global-query` | 同主题 | 海外来源的替代检索词 |
| `--emit` | `md` | `md`、`json`、`html`、`compact`、`path` |
| `--dry-run` | 关闭 | 只显示计划，不联网、不写报告 |
| `--show-config` | 关闭 | 显示配置状态，不显示凭据值 |
| `--timeout` | `900` | 整体超时秒数 |
| `--global-timeout` | `240` | 海外模块超时秒数 |
| `--cn-source-timeout` | `75` | 中文模块单来源超时秒数 |

## 5. 输出契约

JSON 顶层包括 `topic`、`since`、`until`、`per_platform`、`total`、`date_filtered`、`engines`、`config`、`platforms` 和 `errors`。

每个 `platforms[]` 项包括：

```text
id / label / engine / count / raw_count / date_from / date_to / error / items
```

`engine` 使用项目自己的类型标识：

- `platform-search`
- `platform-search-web`

## 6. 设计与限制

- 一次调用只产出一份报告，三种文件格式使用同一个结果快照。
- 先去重、按日期窗口过滤和排序，再应用每来源上限。
- 无日期条目保留，但会标记“日期未知”。
- 平台访问量受公开接口、分页、登录态、网络和风控限制，21 个来源不等于 21 份独立索引，也不承诺全网全量覆盖。
- Google 遇到 IP 风控时降级为可点击搜索链接，不绕过验证码。
- TikTok、Instagram 等来源在组合检索时可自动拆分子批，再合并为一份报告。

## 7. 维护

```bash
python {{SKILL_DIR}}/scripts/sync_vendor.py --dry-run
python {{SKILL_DIR}}/scripts/package_filtered.py --out-dir dist
```

完整性检查不会同步其他目录。项目的唯一 Python 基线由 `pyproject.toml` 声明为 Python 3.11+。

## 8. 合规

仅供学习、研究与个人知识整理。遵守各平台服务条款和 `robots.txt`，控制请求频率，不进行大规模采集、个人数据收集或违法用途。
