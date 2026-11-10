"""GitHub 源（opt-in 海外源，issue #9）。

Author: Jesse (https://github.com/Jesseovo)

GitHub REST Search（未认证 10 次/分钟，配置 ``GITHUB_TOKEN`` 可提高额度）：
- 时间窗内有推送的相关仓库（按 Star 排序）
- 与关键词相关的仓库（检索 name/description/readme，不再要求时间窗内推送）
- 时间窗内新建的相关 Issue / PR / 讨论帖

单条仓库查询的 ``per_page`` 上限为 100，因此按 depth 档位分配每类查询的条数；
多组查询结果按 URL 去重后统一排序。
"""

import sys
import urllib.parse
from typing import Any, Dict, List, Optional

from . import http, relevance

API = "https://api.github.com"


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": http.USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _repo_search(
    query: str,
    per_page: int,
    sort: str,
    token: Optional[str],
    errors: List[str],
) -> List[Dict[str, Any]]:
    params = {"q": query, "sort": sort, "order": "desc", "per_page": min(100, max(1, per_page))}
    try:
        data = http.get(
            f"{API}/search/repositories?{urllib.parse.urlencode(params)}",
            headers=_headers(token), timeout=20, retries=1,
        )
    except http.HTTPError as exc:
        errors.append(_explain(exc))
        return []
    return list((data or {}).get("items") or [])


def _issue_search(
    query: str,
    per_page: int,
    token: Optional[str],
    errors: List[str],
) -> List[Dict[str, Any]]:
    params = {"q": query, "per_page": min(100, max(1, per_page))}
    try:
        data = http.get(
            f"{API}/search/issues?{urllib.parse.urlencode(params)}",
            headers=_headers(token), timeout=20, retries=1,
        )
    except http.HTTPError as exc:
        errors.append(_explain(exc))
        return []
    return list((data or {}).get("items") or [])


def search_github(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search repositories + issues related to the query."""
    # 三组查询各自取多少条；deep 档合计最多约 100 条仓库 + 30 条 issue/PR。
    repo_limit = {"quick": 20, "default": 40, "deep": 60}.get(depth, 40)
    issue_limit = {"quick": 5, "default": 15, "deep": 30}.get(depth, 15)
    items: List[Dict[str, Any]] = []
    errors: List[str] = []

    # A. 时间窗内仍有推送的仓库：反映"最近还在维护"的项目。
    active = [parse_repo(repo, to_date) for repo in _repo_search(
        f"{query} pushed:{from_date}..{to_date}", repo_limit, "stars", token, errors)]
    # B. 名称/描述匹配的仓库：不受推送时间限制，能捞到长期不更新但有参考价值的项目。
    #    不用 in:readme——README 里随手提一句 WebGIS 的仓库会带来大量无关结果。
    matched = [parse_repo(repo, to_date) for repo in _repo_search(
        f"{query} in:name,description", repo_limit, "stars", token, errors)]

    seen_urls = set()
    for item in active + matched:
        url = item.get("url") or ""
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        items.append(item)

    issue_q = f"{query} created:{from_date}..{to_date}"
    for issue in _issue_search(issue_q, issue_limit, token, errors):
        parsed = parse_issue(issue)
        url = parsed.get("url") or ""
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        items.append(parsed)

    if not items and errors:
        raise http.HTTPError("GitHub 搜索失败：" + "；".join(dict.fromkeys(errors)))
    if errors:
        sys.stderr.write(f"[GitHub] 部分请求失败：{'；'.join(dict.fromkeys(errors))}\n")

    for item in items:
        item["relevance"] = relevance.token_overlap_relevance(query, f"{item['title']} {item.get('text', '')}")
    items.sort(key=lambda x: x.get("relevance", 0), reverse=True)
    for i, item in enumerate(items):
        item["id"] = f"GH{i+1}"
    return items


def _explain(exc: http.HTTPError) -> str:
    if exc.status_code == 403 or exc.status_code == 429:
        return "触发 GitHub 搜索限流（未认证 10 次/分钟），可配置 GITHUB_TOKEN"
    if exc.status_code == 422:
        return "查询语法不被 GitHub 接受（可用 --global-query 提供英文关键词）"
    return str(exc)


def parse_repo(repo: Dict[str, Any], to_date: str = "") -> Dict[str, Any]:
    pushed = (repo.get("pushed_at") or "")[:10] or None
    created = (repo.get("created_at") or "")[:10]
    description = repo.get("description") or ""
    language = repo.get("language") or ""
    meta = " · ".join(x for x in (language, f"创建于 {created}" if created else "") if x)
    return {
        "platform": "github",
        "title": f"{repo.get('full_name', '')}: {description}".strip(": "),
        "url": repo.get("html_url") or "",
        "text": f"{description} {meta}".strip(),
        "author": (repo.get("owner") or {}).get("login", ""),
        "container": "仓库",
        "date": pushed,
        "date_confidence": "med" if pushed else "low",
        "engagement": {"stars": repo.get("stargazers_count") or 0, "comments": repo.get("open_issues_count") or 0},
        "why_relevant": f"GitHub 仓库：★{repo.get('stargazers_count', 0)}，最近推送 {pushed or '未知'}",
        "source": "search/repositories",
    }


def parse_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    repo_url = issue.get("repository_url") or ""
    repo = "/".join(repo_url.rstrip("/").split("/")[-2:]) if repo_url else ""
    kind = "PR" if "pull_request" in issue else "Issue"  # the key's presence marks a PR
    reactions = (issue.get("reactions") or {}).get("total_count") or 0
    created = (issue.get("created_at") or "")[:10] or None
    body = (issue.get("body") or "")[:300]
    return {
        "platform": "github",
        "title": issue.get("title") or "",
        "url": issue.get("html_url") or "",
        "text": body,
        "author": (issue.get("user") or {}).get("login", ""),
        "container": f"{repo} {kind}".strip(),
        "date": created,
        "engagement": {"likes": reactions, "comments": issue.get("comments") or 0},
        "why_relevant": f"GitHub {kind}（{repo}）：{issue.get('comments', 0)} 条评论",
        "source": "search/issues",
    }
