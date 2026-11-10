#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开搜索引擎聚合层 —— platform-search 的第三个数据来源。

自建零依赖实现：6 个引擎的 URL 模板按各站公开搜索入口构造，
结果解析用通用 <a> 提取 + 中转链接解码，不依赖任何外部技能包或 API Key。

三类来源在 platform-search 中的分工：
  * platform-search 国内适配器 -> 国内 8 平台 + HN/GitHub/Reddit
  * platform-search 海外适配器 -> X/YouTube/TikTok/Instagram 等海外平台
  * 本模块                -> 国内外公开搜索引擎（网页结果，作为平台检索的补充）

合规：仅请求公开搜索页，不模拟登录、不规避验证码；遇验证码立即停止该引擎；
默认顺序请求 + 间隔，不做并发轰炸。
"""

import base64
import html
import http.cookiejar
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 单一配置源：id -> (中文标签, 分组, URL 模板, 自身域名)
ENGINE_SPECS = {
    # ---- 国内 4 个（大陆直连）----
    "bing_cn": ("必应中国（搜索）", "cn", "https://cn.bing.com/search?q={q}&ensearch=0", "bing.com"),
    "bing_int": ("必应国际入口（搜索）", "cn", "https://cn.bing.com/search?q={q}&ensearch=1", "bing.com"),
    "so360": ("360 搜索（搜索）", "cn", "https://www.so.com/s?q={q}", "so.com"),
    "sogou": ("搜狗（搜索）", "cn", "https://www.sogou.com/web?query={q}", "sogou.com"),
    # ---- 国际 2 个（需可访问国际网络；Google 走浏览器模式）----
    "google": ("Google（搜索）", "global", "https://www.google.com/search?q={q}&num=20", "google.com"),
    "duckduckgo": ("DuckDuckGo（搜索）", "global", "https://html.duckduckgo.com/html/?q={q}", "duckduckgo.com"),
}

ENGINE_LABELS = {k: v[0] for k, v in ENGINE_SPECS.items()}
CN_ENGINES = [k for k, v in ENGINE_SPECS.items() if v[1] == "cn"]
GLOBAL_ENGINES = [k for k, v in ENGINE_SPECS.items() if v[1] == "global"]
DEFAULT_ENGINES = ["bing_cn", "bing_int", "so360", "sogou", "google", "duckduckgo"]

GROUPS = {
    "cn": CN_ENGINES,
    "global": GLOBAL_ENGINES,
    "all": CN_ENGINES + GLOBAL_ENGINES,
    "default": DEFAULT_ENGINES,
}

# 搜索引擎结果里要排除的自身/跳转域名
_INTERNAL_HOSTS = {
    "bing.com", "baidu.com", "sogou.com", "so.com", "360.cn", "sm.cn",
    "duckduckgo.com", "google.com", "google.com.hk", "ecosia.org", "yahoo.com",
    "startpage.com", "brave.com", "qwant.com", "wolframalpha.com",
    "microsoft.com", "go.microsoft.com", "support.microsoft.com", "live.com",
    "gstatic.com", "googleusercontent.com", "bingstatic.com",
}
_TRACK_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "src", "ref", "referer", "fbclid", "gclid", "wd", "eqid",
}
_CAPTCHA_MARKERS = ("验证码", "captcha", "unusual traffic", "人机校验", "安全验证", "verify you are human")

_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)
_MDURL_RE = re.compile(r"data-mdurl\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w-]+)", re.I)


def resolve_engines(spec: str):
    """展开 --web-engines，支持 id 与分组 cn/global/all/default。"""
    ids = []
    for raw in (spec or "").split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in GROUPS:
            ids.extend(GROUPS[token])
        elif token in ENGINE_SPECS:
            ids.append(token)
        else:
            raise ValueError(
                f"未知搜索引擎 '{token}'。可用: {', '.join(ENGINE_SPECS)} "
                "或分组 cn / global / all / default"
            )
    return list(dict.fromkeys(ids))


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", _TAG_RE.sub("", text or ""))).strip()


def _unwrap(url: str) -> str:
    """还原 Bing / DuckDuckGo 的中转跳转链接。"""
    try:
        parts = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parts.query)
        if "bing.com/ck/a" in url:
            raw = query.get("u", [""])[0]
            if raw.startswith("a1"):
                payload = raw[2:]
                decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
                return decoded.decode("utf-8", "replace")
        if "duckduckgo.com/l/" in url:
            return query.get("uddg", [url])[0]
    except Exception:
        pass
    return url


def _normalize(url: str) -> str:
    """去掉常见跟踪参数，用于跨引擎去重。"""
    try:
        p = urllib.parse.urlparse(url)
        pairs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                 if k.lower() not in _TRACK_PARAMS]
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, p.params,
                                        urllib.parse.urlencode(pairs), ""))
    except Exception:
        return url


def _internal(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return True
    return any(host == h or host.endswith("." + h) for h in _INTERNAL_HOSTS)


def _extract_date(text: str) -> str:
    m = _DATE_RE.search(text or "")
    if not m:
        return ""
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def build_url(eid: str, query: str, since: str = "", until: str = "", vertical: str = "") -> str:
    """构造搜索 URL；时间参数只对公开支持的引擎生效。"""
    spec = ENGINE_SPECS[eid]
    words = query.strip()
    if vertical:
        words = f"{words} site:{vertical}"
    url = spec[2].replace("{q}", urllib.parse.quote_plus(words))
    if since:
        try:
            s = date.fromisoformat(since)
            u = date.fromisoformat(until) if until else date.today()
            if eid in ("google", "google_hk"):
                url += f"&tbs=cdr:1,cd_min={s.month}/{s.day}/{s.year},cd_max={u.month}/{u.day}/{u.year}"
            elif eid in ("duckduckgo", "ecosia"):
                url += f"&df={s.isoformat()}..{u.isoformat()}"
        except ValueError:
            pass
    return url


def _decode(raw: bytes) -> str:
    head = raw[:2048].decode("latin-1", "replace")
    m = _CHARSET_RE.search(head)
    enc = (m.group(1) if m else "utf-8").lower()
    try:
        text = raw.decode(enc, "replace")
    except (LookupError, UnicodeError):
        text = raw.decode("utf-8", "replace")
    if text.count("\ufffd") > 20:
        try:
            text = raw.decode("gb18030", "replace")
        except UnicodeError:
            pass
    return text


def _fetch(opener, url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    with opener.open(req, timeout=timeout) as resp:
        return _decode(resp.read())


def _link_row(eid: str, url: str, note: str):
    """抓取失败时的兜底：至少给出可点击的搜索链接，不让该引擎空手。"""
    return {
        "platform": eid,
        "title": f"{ENGINE_LABELS[eid]}：{note}",
        "url": url,
        "date": "",
        "author": "",
        "engagement": "",
        "snippet": "",
    }


def _parse(body: str, eid: str, limit: int):
    rows, seen = [], set()
    for attrs, inner in _A_RE.findall(body):
        href_m = _HREF_RE.search(attrs)
        if not href_m:
            continue
        href = _MDURL_RE.search(attrs).group(1) if _MDURL_RE.search(attrs) else href_m.group(1)
        href = html.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            continue
        url = _unwrap(href)
        if not url.lower().startswith("http"):
            continue
        title = _clean(inner)
        if len(title) < 6 or _internal(url):
            continue
        key = _normalize(url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "platform": eid,
            "title": title[:220],
            "url": url,
            "date": _extract_date(title + " " + _clean(attrs)),
            "author": "",
            "engagement": "",
            "snippet": "",
        })
        if len(rows) >= limit:
            break
    return rows


def _google_via_browser(url, limit, timeout=10):
    """Google 结果页需要 JavaScript，用本机 Chromium 打开并解析 h3 + 结果链接。

    只访问公开搜索页，不模拟登录、不绕过验证码。返回 ``(rows, reason)``：
    能解析出结果时 reason 为空串；失败时 rows 为空并说明真实原因
    （Google 现在会直接跳到 /sorry/index 人机校验页，不是「需要 JavaScript」）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "未安装 playwright，无法用浏览器模式抓 Google"
    exe = os.environ.get("PLATFORM_SEARCH_BROWSER_PATH", "").strip()
    kwargs = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
    if exe and os.path.isfile(exe):
        kwargs["executable_path"] = exe
    rows = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**kwargs)
            ctx = browser.new_context(user_agent=UA, locale="zh-CN")
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=max(15000, int(timeout) * 1000))
            page.wait_for_timeout(2500)
            heads = page.query_selector_all("h3")
            # 真验证码页不会有 h3 结果；不尝试绕过，直接降级并说明原因。
            low = page.content().lower()
            if not heads and any(k in low for k in ("unusual traffic", "/sorry/", "captcha")):
                browser.close()
                return [], "触发 Google 人机校验（异常流量确认页），已降级为搜索链接"
            for h in heads[:limit]:
                try:
                    title = (h.inner_text() or "").strip()
                    a = h.query_selector("xpath=ancestor::a[1]")
                    href = a.get_attribute("href") if a else ""
                    if not title or not href:
                        continue
                    abs_url = urllib.parse.urljoin(page.url, href)
                    real_url = abs_url
                    # Google 用 /goto?url= 加密中转；跟随重定向拿到真实目标 URL。
                    try:
                        resp = ctx.request.get(abs_url, max_redirects=5, timeout=6000)
                        final = resp.url
                        if final and "google." not in urllib.parse.urlparse(final).netloc:
                            real_url = final
                    except Exception:
                        pass
                    rows.append({
                        "title": title[:220], "url": real_url, "date": "",
                        "author": "", "engagement": "", "snippet": "",
                    })
                except Exception:
                    continue
            browser.close()
    except Exception as exc:
        return [], f"浏览器抓取失败：{type(exc).__name__}"
    return rows, ""


def search_web(topic, engine_ids, since="", until="", vertical="",
               limit=20, delay=1.5, timeout=10, log=None):
    """按引擎顺序检索，返回 (rows_by_engine, errors)。"""
    rows_by_engine, errors = {}, {}
    limit = max(1, min(int(limit), 50))
    for idx, eid in enumerate(engine_ids):
        if idx and delay > 0:
            time.sleep(delay)
        url = build_url(eid, topic, since, until, vertical)
        browser_reason = ""
        if eid == "google":
            browser_rows, browser_reason = _google_via_browser(url, limit, timeout)
            if browser_rows:
                rows_by_engine[eid] = browser_rows
                if log:
                    log(f"[搜索-{ENGINE_LABELS[eid]}] {len(browser_rows)} 条（浏览器模式）")
                continue
        if log:
            log(f"[搜索-{ENGINE_LABELS[eid]}] 请求中...")
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        body = ""
        try:
            body = _fetch(opener, url, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 418, 429, 503) and delay > 0:
                # 一次会话重试：先取首页拿 Cookie，等待后重试一次
                try:
                    time.sleep(2)
                    _fetch(opener, urllib.parse.urlunparse(
                        urllib.parse.urlparse(url)[:2] + ("/", "", "", "")), timeout)
                    body = _fetch(opener, url, timeout)
                except Exception as exc2:
                    errors[eid] = f"HTTP {exc.code}，重试失败: {type(exc2).__name__}"
            else:
                errors[eid] = f"HTTP {exc.code}"
        except Exception as exc:
            errors[eid] = f"{type(exc).__name__}: {str(exc)[:120]}"
        if not body:
            rows_by_engine[eid] = [_link_row(eid, url, "抓取失败，点击打开搜索页")]
            continue
        if any(marker.lower() in body.lower() for marker in _CAPTCHA_MARKERS):
            errors[eid] = browser_reason or "触发验证码/人机校验，已停止该引擎请求"
            rows_by_engine[eid] = [_link_row(eid, url, "触发验证码，点击打开搜索页")]
            continue
        rows = _parse(body, eid, limit)
        if not rows and eid == "duckduckgo" and delay > 0:
            # DDG 的 html 端点会间歇性返回空壳页（实测同一查询 10 条 / 1 条来回跳），
            # 等两秒重试一次即可，不必让用户重跑。
            try:
                time.sleep(2)
                body = _fetch(opener, url, timeout)
                rows = _parse(body, eid, limit)
                if rows and log:
                    log(f"[搜索-{ENGINE_LABELS[eid]}] 重试成功 {len(rows)} 条")
            except Exception:
                pass
        if not rows:
            if browser_reason:
                # 浏览器已经给出真实原因（如 Google 人机校验页），优先保留；
                # HTTP 分支看到的 enablejs 只是同一风控的另一种表现。
                errors[eid] = browser_reason
                rows = [_link_row(eid, url, "人机校验，点击打开搜索页")]
            elif "enablejs" in body.lower() or "/httpservice/retry/" in body:
                errors[eid] = "站点返回需启用 JavaScript 的页面，纯 HTTP 无法解析结果"
                rows = [_link_row(eid, url, "站点需要 JavaScript，点击打开搜索页")]
            else:
                errors[eid] = browser_reason or "未解析到结果（页面结构变化或反爬）"
                rows = [_link_row(eid, url, "未解析到结果，点击打开搜索页")]
        else:
            if log:
                log(f"[搜索-{ENGINE_LABELS[eid]}] {len(rows)} 条")
        rows_by_engine[eid] = rows
    return rows_by_engine, errors
