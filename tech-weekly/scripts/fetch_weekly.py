#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tech Weekly aggregator: fetch multiple sources and generate a Hugo weekly markdown.

Usage:
  python scripts/fetch_weekly.py --output content/weekly --max-per-source 20 --timeout 15 --draft false

新增参数:
  --skip-check true/false  跳过已抓取链接检查（默认 false）
  --overwrite true/false   覆盖 SQLite 中已有记录（默认 false）
"""
import os
import re
import sys
import time
import json
import hashlib
import argparse
import datetime as dt
from urllib.parse import urljoin, urlparse
import traceback

import requests
from bs4 import BeautifulSoup
import feedparser
import sqlite3
import random

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
    "TechWeeklyBot/1.0"
)
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}

Item = dict  # {title, url, source, summary}

# ---------------------------- helpers ----------------------------

def log(msg: str):
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fetch(url: str, timeout: int = 15) -> str:
    log(f"GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def fetch_with_retries(url: str, timeout: int = 15, retries: int = 3, backoff: float = 0.8) -> str:
    last_err = None
    for i in range(retries):
        try:
            return fetch(url, timeout=timeout)
        except Exception as e:
            last_err = e
            log(f"WARN: fetch failed ({i+1}/{retries}) {url}: {e}")
            time.sleep(backoff * (2 ** i))
    if last_err:
        raise last_err
    return ""


def health_check(url: str, timeout: int = 5) -> tuple[bool, str]:
    try:
        html = fetch(url, timeout=timeout)
        ok = bool(html and len(html) > 256)
        return ok, "ok" if ok else "html_too_short"
    except Exception as e:
        return False, f"error: {e}"


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def dedup(items: list[Item]) -> list[Item]:
    seen = set()
    out = []
    for it in items:
        key = hashlib.sha1(norm_space(it.get("url", "")).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

# ---------------------------- sqlite cache ----------------------------

def get_db_path() -> str:
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(cwd)
    return os.path.join(repo_root, "weekly_cache.db")


def get_db() -> sqlite3.Connection:
    path = get_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fetched (
            url TEXT PRIMARY KEY,
            title TEXT,
            source TEXT,
            first_seen TEXT,
            times_seen INTEGER DEFAULT 0
        )
        """
    )
    return conn


def has_seen(conn: sqlite3.Connection, url: str) -> bool:
    url_norm = norm_space(url)
    cur = conn.execute("SELECT 1 FROM fetched WHERE url = ?", (url_norm,))
    return cur.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, it: Item, seen_time: str):
    url_norm = norm_space(it.get("url", ""))
    title = norm_space(it.get("title", ""))
    source = norm_space(it.get("source", ""))
    conn.execute(
        """
        INSERT INTO fetched (url, title, source, first_seen, times_seen)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(url) DO UPDATE SET times_seen = times_seen + 1
        """,
        (url_norm, title, source, seen_time),
    )
    conn.commit()

# 覆盖写入：无条件覆盖数据库中匹配记录（title/source/first_seen/times_seen）
def mark_seen_overwrite(conn: sqlite3.Connection, it: Item, seen_time: str):
    url_norm = norm_space(it.get("url", ""))
    title = norm_space(it.get("title", ""))
    source = norm_space(it.get("source", ""))
    conn.execute(
        """
        INSERT INTO fetched (url, title, source, first_seen, times_seen)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(url) DO UPDATE SET
          title = excluded.title,
          source = excluded.source,
          first_seen = excluded.first_seen,
          times_seen = 1
        """,
        (url_norm, title, source, seen_time),
    )
    conn.commit()

# ---------------------------- parsers ----------------------------

def parse_github_ruanyf_issues(html: str) -> list[Item]:
    soup = make_soup(html)
    items: list[Item] = []
    # GitHub issues list
    for li in soup.select("div[aria-label='Issues'] div[role='group'] a[id^='issue_'], a.Link--primary"):
        title = norm_space(li.get_text())
        href = li.get("href") or ""
        if href and not href.startswith("http"):
            href = urljoin("https://github.com/ruanyf/weekly/issues", href)
        if "/issues/" in href:
            items.append({"title": title, "url": href, "source": "ruanyf_weekly", "summary": ""})
    return items


def parse_github_trending(html: str) -> list[Item]:
    soup = make_soup(html)
    items: list[Item] = []
    for art in soup.select("article"):  # trending uses <article> blocks
        a = art.select_one("h2 a")
        if not a:
            a = art.select_one("h1 a, .h3 a")
        if not a:
            continue
        repo = norm_space(a.get_text())
        href = a.get("href") or ""
        if href and not href.startswith("http"):
            href = urljoin("https://github.com", href)
        desc_el = art.select_one("p")
        desc = norm_space(desc_el.get_text()) if desc_el else ""
        items.append({"title": repo, "url": href, "source": "github_trending", "summary": desc})
    return items


def parse_daemonology_hn_weekly(html: str) -> list[Item]:
    soup = make_soup(html)
    items: list[Item] = []
    # The page lists anchors for top HN posts
    for a in soup.select("a[href^='https://']"):
        text = norm_space(a.get_text())
        href = a.get("href") or ""
        if not text or not href:
            continue
        # Skip internal anchors that aren't articles
        if "daemonology.net" in href:
            continue
        items.append({"title": text, "url": href, "source": "hn_weekly", "summary": ""})
    return items


def parse_generic(url: str, html: str, picks: int = 40) -> list[Item]:
    soup = make_soup(html)
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    items: list[Item] = []
    for a in soup.select("a[href]"):
        text = norm_space(a.get_text())
        href = a.get("href") or ""
        if not text or len(text) < 5:
            continue
        # normalize href
        if href.startswith("/"):
            href = urljoin(origin, href)
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        # prefer external or same-origin links
        items.append({"title": text, "url": href, "source": origin, "summary": ""})
        if len(items) >= picks:
            break
    return items


def parse_feed(url: str) -> list[Item]:
    d = feedparser.parse(url)
    items: list[Item] = []
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    for e in d.entries[:40]:
        title = norm_space(getattr(e, "title", ""))
        link = getattr(e, "link", "")
        summary = norm_space(getattr(e, "summary", ""))
        if title and link:
            items.append({"title": title, "url": link, "source": origin, "summary": summary})
    return items

# ---------------------------- orchestrator ----------------------------

SOURCES = [
    ("https://github.com/ruanyf/weekly/issues", parse_github_ruanyf_issues),
    ("https://github.com/trending", parse_github_trending),
    ("https://hn.aimaker.dev/", None),  # generic
    ("https://www.daemonology.net/hn-weekly/", parse_daemonology_hn_weekly),
    ("https://koala-oss.app/news/", None),
    ("https://topsub.cc/", None),
    ("https://git.news/", None),
    ("https://github.com/howie6879/weekly", None),
    ("https://github.com/tw93/weekly?tab=readme-ov-file", None),
    ("https://github.com/ljinkai/weekly", None),
    ("https://hn.buzzing.cc/", None),
    ("https://morerss.com/zh", None),
    ("https://x-daily.pages.dev/", None),
    ("https://decohack.com/", None),
    ("https://github.com/headllines/hackernews-monthly", None),
    ("https://weekly.howie6879.com/", None),
    ("https://www.zhihu.com/people/githubdaily", None),
    ("https://github.com/GitHubDaily/GitHubDaily", None),
    ("https://x.com/GitHub_Daily", None),
    ("https://hellogithub.com/", None),
    ("https://github.com/OpenGithubs/github-weekly-rank", None),
    ("https://open.itc.cn/", None),
    ("https://www.github-zh.com/top", None),
]

SECTION_MAP = {
    "github_trending": "开源项目",
    "https://git.news": "开源项目",
    "https://github.com": "阅读推荐",
    "ruanyf_weekly": "阅读推荐",
    "hn_weekly": "阅读推荐",
    "https://hn.aimaker.dev": "阅读推荐",
    "https://hn.buzzing.cc": "阅读推荐",
    "https://koala-oss.app": "趋势观察",
    "https://topsub.cc": "趋势观察",
    "https://morerss.com": "趋势观察",
    "https://x-daily.pages.dev": "趋势观察",
    "https://decohack.com": "趋势观察",
    "https://weekly.howie6879.com": "阅读推荐",
    "https://www.zhihu.com": "阅读推荐",
    "https://x.com": "趋势观察",
    "https://hellogithub.com": "开源项目",
    "https://open.itc.cn": "趋势观察",
    "https://www.github-zh.com": "趋势观察",
    # releases from bookmarks
    "release_github": "新版本发布",
    "release_gitee": "新版本发布",
}

DEFAULT_SECTIONS = ["趋势观察", "开源项目", "新版本发布", "阅读推荐"]


def classify_item(it: Item) -> str:
    src = it.get("source", "")
    # direct key
    if src in SECTION_MAP:
        return SECTION_MAP[src]
    # origin domain matching
    origin = f"https://{urlparse(src).netloc}" if src.startswith("http") else src
    for k, v in SECTION_MAP.items():
        if origin.startswith(k):
            return v
    # fallback
    return "阅读推荐"


def build_markdown(items: list[Item], title: str, date_str: str, draft: bool = False) -> str:
    # group by section
    grouped: dict[str, list[Item]] = {s: [] for s in DEFAULT_SECTIONS}
    for it in items:
        sec = classify_item(it)
        grouped.setdefault(sec, []).append(it)
    # summary: top 5 titles
    highlights = [it["title"] for it in items[:5]]
    summary = "本期精选：" + "、".join(highlights)
    fm = [
        "---",
        f"title: \"{title}\"",
        f"date: {date_str}",
        f"draft: {'true' if draft else 'false'}",
        "tags: [\"周报\",\"开源\",\"科技\"]",
        "categories: [\"Weekly\"]",
        f"summary: \"{summary}\"",
        "---",
        "",
    ]
    # sections
    lines = fm
    for sec in DEFAULT_SECTIONS:
        lines.append(f"## {sec}")
        sec_items = grouped.get(sec, [])[:20]
        if not sec_items:
            lines.append("- 暂无")
        else:
            for it in sec_items:
                desc = f" — {it['summary']}" if it.get("summary") else ""
                # escape brackets in title
                title_clean = it["title"].replace("[", "【").replace("]", "】")
                lines.append(f"- [{title_clean}]({it['url']}){desc}")
        lines.append("")
    return "\n".join(lines)


def iso_week_slug(date: dt.date) -> str:
    year, week, _ = date.isocalendar()
    return f"{year}-{week:02d}"


class Stats:
    def __init__(self):
        self.sources = {}
        self.items_total = 0
        self.failures = 0
        self.started_at = time.time()
        self.finished_at = None
    def start_source(self, url: str):
        self.sources[url] = {"ok": False, "count": 0, "error": "", "duration": 0.0}
        self.sources[url]["_start"] = time.time()
    def finish_source(self, url: str, ok: bool, count: int, err: str = ""):
        s = self.sources.get(url, {})
        s["ok"] = ok
        s["count"] = count
        s["error"] = err
        s["duration"] = time.time() - s.get("_start", time.time())
        self.sources[url] = s
        if not ok:
            self.failures += 1
    def finish(self, total_items: int):
        self.items_total = total_items
        self.finished_at = time.time()


def extract_main_text(html: str) -> str:
    soup = make_soup(html)
    candidates = []
    selectors = [
        "article",
        "main",
        ".post",
        ".entry-content",
        "#content",
        "[role='main']",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            text = norm_space(el.get_text(separator=" "))
            if len(text) > 200:
                candidates.append(text)
    if not candidates:
        # fallback: pick longest paragraph sequence
        paras = [norm_space(p.get_text()) for p in soup.select("p")]
        paras = [p for p in paras if len(p) > 50]
        joined = "\n\n".join(paras)
        return joined[:5000]
    # pick longest
    best = max(candidates, key=len)
    return best[:8000]


def _get_meta(soup: BeautifulSoup, name: str) -> str | None:
    el = soup.select_one(f"meta[name='{name}']") or soup.select_one(f"meta[property='{name}']")
    return norm_space(el.get("content") or "") if el else None


def extract_metadata_from_html(url: str, html: str) -> dict:
    soup = make_soup(html)
    title = _get_meta(soup, "og:title") or _get_meta(soup, "twitter:title")
    if not title:
        t_el = soup.select_one("title")
        title = norm_space(t_el.get_text()) if t_el else ""
    author = _get_meta(soup, "article:author") or _get_meta(soup, "author")
    if not author:
        a_el = soup.select_one(".author, .byline, a[rel='author']")
        author = norm_space(a_el.get_text()) if a_el else ""
    pub = _get_meta(soup, "article:published_time") or _get_meta(soup, "date")
    if not pub:
        t_el = soup.select_one("time[datetime]")
        pub = t_el.get("datetime") if t_el else ""
    content = extract_main_text(html)
    return {
        "title": title,
        "author": author,
        "published_at": norm_space(pub or ""),
        "content": content,
    }


def tags_from_text(text: str) -> list[str]:
    tags = []
    t = text or ""
    for kw in TECH_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", t, flags=re.IGNORECASE):
            tags.append(kw)
    return sorted(set(tags))


def enrich_items(items: list[Item], deep_scan: bool, timeout: int, retries: int, backoff: float, stats: Stats) -> list[Item]:
    enriched: list[Item] = []
    for it in items:
        meta = {}
        try:
            if deep_scan:
                html_item = fetch_with_retries(it.get("url", ""), timeout=timeout, retries=retries, backoff=backoff)
                meta = extract_metadata_from_html(it.get("url", ""), html_item)
            else:
                meta = {"title": it.get("title", ""), "author": "", "published_at": "", "content": ""}
        except Exception as e:
            log(f"WARN: deep-scan failed for {it.get('url','')}: {e}")
            meta = {"title": it.get("title", ""), "author": "", "published_at": "", "content": ""}
        # auto summary from content if missing
        summary_src = it.get("summary") or meta.get("content", "")
        summary = generate_summary(summary_src)
        # tags
        tags = tags_from_text((it.get("title", "") + " " + meta.get("content", "")))
        enriched.append({
            "title": meta.get("title") or it.get("title", ""),
            "url": it.get("url", ""),
            "source": it.get("source", ""),
            "summary": summary,
            "author": meta.get("author", ""),
            "published_at": meta.get("published_at", ""),
            "tags": tags,
        })
    stats.items_total = len(enriched)
    return enriched


def send_alert(webhook: str | None, payload: dict):
    if not webhook:
        return
    try:
        requests.post(webhook, json=payload, timeout=10)
    except Exception as e:
        log(f"WARN: alert webhook failed: {e}")


def generate_report(repo_root: str, slug: str, items: list[Item], stats: Stats, raw_htmls: dict[str, str]):
    reports_dir = os.path.join(repo_root, "tech-weekly", "reports")
    raw_dir = os.path.join(repo_root, "tech-weekly", "raw", slug)
    os.makedirs(reports_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)
    # write JSON report
    json_path = os.path.join(reports_dir, f"{slug}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"slug": slug, "generated_at": dt.datetime.now().isoformat(), "stats": stats.sources, "items": items}, f, ensure_ascii=False, indent=2)
    # write CSV
    csv_path = os.path.join(reports_dir, f"{slug}.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("title,url,source,author,published_at,tags,summary\n")
        for it in items:
            tags = ";".join(it.get("tags", []))
            row = [
                it.get("title", "").replace("\n", " ").replace(",", " "),
                it.get("url", ""),
                it.get("source", ""),
                it.get("author", "").replace(",", " "),
                it.get("published_at", ""),
                tags,
                it.get("summary", "").replace("\n", " ").replace(",", ";"),
            ]
            f.write(",".join(row) + "\n")
    # write raw backups per source page
    for url, html in raw_htmls.items():
        safe = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:80]
        with open(os.path.join(raw_dir, f"{safe}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    log(f"Reports written: {json_path}, {csv_path}; raw backups: {raw_dir}")


def run(max_per_source: int, timeout: int, sleep: float, conn: sqlite3.Connection, retries: int = 3, backoff: float = 0.8, do_health_check: bool = True, skip_check: bool = False) -> tuple[list[Item], dict[str, str], Stats]:
    stats = Stats()
    raw_htmls: dict[str, str] = {}
    all_items: list[Item] = []
    for url, parser in SOURCES:
        stats.start_source(url)
        try:
            if do_health_check:
                ok, reason = health_check(url, timeout=min(timeout, 8))
                if not ok:
                    stats.finish_source(url, False, 0, err=reason)
                    log(f"WARN: health check failed for {url}: {reason}")
                    continue
            html = fetch_with_retries(url, timeout=timeout, retries=retries, backoff=backoff)
            raw_htmls[url] = html
            if parser:
                items = parser(html)
            else:
                items = parse_generic(url, html, picks=max_per_source*2)
            items = items[:max_per_source]
            # 依据 skip_check 控制是否跳过已抓取链接检查
            if skip_check:
                fresh = items
            else:
                fresh: list[Item] = []
                for it in items:
                    if not has_seen(conn, it.get("url", "")):
                        fresh.append(it)
            stats.finish_source(url, True, len(fresh))
            all_items.extend(fresh)
            time.sleep(sleep)
        except Exception as e:
            stats.finish_source(url, False, 0, err=str(e))
            log(f"WARN: failed {url}: {e}\n{traceback.format_exc()}")
            continue
    return dedup(all_items), raw_htmls, stats


# ---------------------------- releases from bookmarks ----------------------------

def _walk_bookmarks_urls(node, out: list[str]):
    if isinstance(node, dict):
        url = node.get("url")
        if url:
            out.append(url)
        children = node.get("children") or []
        for ch in children:
            _walk_bookmarks_urls(ch, out)
    elif isinstance(node, list):
        for ch in node:
            _walk_bookmarks_urls(ch, out)


def load_repo_candidates_from_bookmarks(bookmarks_path: str) -> list[dict]:
    try:
        with open(bookmarks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"WARN: failed to read bookmarks: {e}")
        return []
    urls: list[str] = []
    root = data.get("bookmarks", data)
    _walk_bookmarks_urls(root, urls)
    candidates = []
    seen = set()
    for url in urls:
        if not isinstance(url, str):
            continue
        if ("github.com" in url) or ("gitee.com" in url):
            parsed = urlparse(url)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
                platform = "github" if "github.com" in parsed.netloc else "gitee"
                key = (platform, owner, repo)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({"platform": platform, "owner": owner, "repo": repo})
    allowed = {"apache", "google", "facebook", "facebookresearch", "twitter", "x"}
    filtered = [c for c in candidates if c.get("owner", "").lower() in allowed]
    return random.sample(filtered, k=min(20, len(filtered)))


def github_latest_release(owner: str, repo: str, timeout: int = 12) -> dict | None:
    # Try GitHub API first
    api_headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", headers=api_headers, timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            tag = j.get("tag_name")
            if tag:
                return {
                    "tag": tag,
                    "name": j.get("name") or tag,
                    "url": j.get("html_url") or f"https://github.com/{owner}/{repo}/releases/tag/{tag}",
                    "published_at": j.get("published_at") or "",
                }
    except Exception as e:
        log(f"WARN: github api latest failed {owner}/{repo}: {e}")
    # Fallback to tags API
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/tags", headers=api_headers, timeout=timeout)
        if r.status_code == 200:
            tags = r.json()
            if isinstance(tags, list) and tags:
                tag = (tags[0] or {}).get("name")
                if tag:
                    return {
                        "tag": tag,
                        "name": tag,
                        "url": f"https://github.com/{owner}/{repo}/releases/tag/{tag}",
                        "published_at": "",
                    }
    except Exception as e:
        log(f"WARN: github api tags failed {owner}/{repo}: {e}")
    # Fallback to releases HTML
    try:
        html = fetch_with_retries(f"https://github.com/{owner}/{repo}/releases", timeout=timeout, retries=2, backoff=0.8)
        soup = make_soup(html)
        a = soup.select_one("a[href*='/releases/tag/']")
        if a:
            href = a.get("href") or ""
            tag = href.split("/releases/tag/")[-1]
            if href and tag:
                if not href.startswith("http"):
                    href = urljoin(f"https://github.com/{owner}/{repo}/releases", href)
                name = norm_space(a.get_text()) or tag
                return {"tag": tag, "name": name, "url": href, "published_at": ""}
    except Exception as e:
        log(f"WARN: github releases html failed {owner}/{repo}: {e}")
    return None


def gitee_latest_release(owner: str, repo: str, timeout: int = 12) -> dict | None:
    # Prefer releases HTML
    try:
        html = fetch_with_retries(f"https://gitee.com/{owner}/{repo}/releases", timeout=timeout, retries=2, backoff=0.8)
        soup = make_soup(html)
        a = soup.select_one("a[href*='/releases/tag/']")
        if a:
            href = a.get("href") or ""
            tag = href.split("/releases/tag/")[-1]
            if href and tag:
                if not href.startswith("http"):
                    href = urljoin(f"https://gitee.com/{owner}/{repo}/releases", href)
                name = norm_space(a.get_text()) or tag
                return {"tag": tag, "name": name, "url": href, "published_at": ""}
    except Exception as e:
        log(f"WARN: gitee releases html failed {owner}/{repo}: {e}")
    # Fallback to tags page
    try:
        html = fetch_with_retries(f"https://gitee.com/{owner}/{repo}/tags", timeout=timeout, retries=2, backoff=0.8)
        soup = make_soup(html)
        # Attempt to find tag anchors
        a = soup.select_one("a[href*='/tags/'], a[href*='/tree/']")
        if a:
            href = a.get("href") or ""
            # Try to infer tag from last path segment
            parsed = urlparse(href)
            parts = [p for p in parsed.path.split("/") if p]
            tag = parts[-1] if parts else ""
            if href and tag:
                if not href.startswith("http"):
                    href = urljoin(f"https://gitee.com/{owner}/{repo}/tags", href)
                name = norm_space(a.get_text()) or tag
                # Construct releases URL if possible
                rel_url = f"https://gitee.com/{owner}/{repo}/releases/tag/{tag}"
                return {"tag": tag, "name": name, "url": rel_url, "published_at": ""}
    except Exception as e:
        log(f"WARN: gitee tags html failed {owner}/{repo}: {e}")
    return None


def collect_release_items(bookmarks_path: str, conn: sqlite3.Connection, skip_check: bool, timeout: int = 12) -> list[Item]:
    items: list[Item] = []
    repos = load_repo_candidates_from_bookmarks(bookmarks_path)
    for r in repos:
        info = None
        if r["platform"] == "github":
            info = github_latest_release(r["owner"], r["repo"], timeout=timeout)
            src = "release_github"
        else:
            info = gitee_latest_release(r["owner"], r["repo"], timeout=timeout)
            src = "release_gitee"
        if not info:
            continue
        it = {
            "title": f"{r['owner']}/{r['repo']} {info.get('tag','')}",
            "url": info.get("url", ""),
            "source": src,
            "summary": info.get("name", ""),
        }
        if skip_check or not has_seen(conn, it["url"]):
            items.append(it)
    return items


def main():
    ap = argparse.ArgumentParser(description="Fetch sources and generate weekly markdown")
    ap.add_argument("--output", default="content/weekly", help="Output directory under tech-weekly")
    ap.add_argument("--max-per-source", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--sleep", type=float, default=0.8, help="sleep seconds between sources")
    ap.add_argument("--draft", type=lambda x: x.lower() == "true", default=False)
    ap.add_argument("--date", type=str, help="YYYY-MM-DD weekly date")
    ap.add_argument("--title", type=str, default=None, help="Custom title")
    # HN-only segment update options
    ap.add_argument("--hn-only", type=lambda x: x.lower() == "false", default=False, help="Fetch HN and update a segment")
    ap.add_argument("--segment-file", type=str, help="Markdown file to update (relative to repo root or absolute)")
    ap.add_argument("--segment-start", type=int, help="Start line (1-indexed)")
    ap.add_argument("--segment-end", type=int, help="End line (1-indexed)")
    # New robustness and reporting options
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--retry-backoff", type=float, default=0.8)
    ap.add_argument("--health-check", type=lambda x: x.lower() == "true", default=True)
    ap.add_argument("--deep-scan", type=lambda x: x.lower() == "true", default=False, help="Fetch each item page to extract metadata")
    ap.add_argument("--report", type=lambda x: x.lower() == "true", default=True, help="Generate structured JSON/CSV report and raw backups")
    ap.add_argument("--monitor-webhook", type=str, default=os.environ.get("ALERT_WEBHOOK", ""), help="Webhook to send alerts on failures")
    ap.add_argument("--daemon", type=lambda x: x.lower() == "true", default=False, help="Run continuously with interval")
    ap.add_argument("--interval", type=int, default=3600, help="Daemon interval seconds")
    # 新增参数：跳过检查与覆盖数据库
    ap.add_argument("--skip-check", type=lambda x: x.lower() == "true", default=False, help="跳过已抓取链接检查（不与 SQLite 缓存比对）")
    ap.add_argument("--overwrite", type=lambda x: x.lower() == "true", default=False, help="强制覆盖 SQLite 数据库中已有记录")
    args = ap.parse_args()

    def one_round():
        conn = get_db()
        # HN-only segment update path
        if args.hn_only and args.segment_file and args.segment_start and args.segment_end:
            try:
                html = fetch_with_retries("https://news.ycombinator.com/", timeout=args.timeout, retries=args.retries, backoff=args.retry_backoff)
                raw_items = parse_hn_front(html)
                # 依据 skip-check 控制是否过滤已见条目
                if args.skip_check:
                    selected = raw_items
                else:
                    fresh: list[Item] = []
                    for it in raw_items:
                        if not has_seen(conn, it.get("url", "")):
                            fresh.append(it)
                    selected = fresh or raw_items
                opt = optimize_items(selected)
                need = max(1, args.segment_end - args.segment_start + 1)
                opt = opt[:need]
                cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                repo_root = os.path.dirname(cwd)
                seg_path = args.segment_file
                if not os.path.isabs(seg_path):
                    seg_path = os.path.join(repo_root, seg_path)
                update_markdown_segment(seg_path, args.segment_start, args.segment_end, opt)
                seen_time = dt.datetime.now().strftime("%Y-%m-%d")
                for it in opt:
                    try:
                        if args.overwrite:
                            mark_seen_overwrite(conn, it, seen_time)
                        else:
                            mark_seen(conn, it, seen_time)
                    except Exception as e:
                        log(f"WARN: failed to record item: {e}")
                log(f"Updated segment: {seg_path}:{args.segment_start}-{args.segment_end}")
            except Exception as e:
                log(f"WARN: HN segment update failed: {e}")
            finally:
                conn.close()
            return

        # aggregate
        items_raw, raw_htmls, stats = run(args.max_per_source, args.timeout, args.sleep, conn, retries=args.retries, backoff=args.retry_backoff, do_health_check=args.health_check, skip_check=args.skip_check)
        # append releases from bookmarks into "新版本发布" section
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_root = os.path.dirname(cwd)
        bookmarks_path = os.path.join(repo_root, "tech-weekly", "data", "bookmarks", "bookmarks.json")
        release_items = collect_release_items(bookmarks_path, conn, skip_check=args.skip_check, timeout=args.timeout)
        if release_items:
            log(f"Collected {len(release_items)} release items from bookmarks")
            items_raw.extend(release_items)
        # enrich with metadata if requested
        items = enrich_items(items_raw, deep_scan=args.deep_scan, timeout=args.timeout, retries=args.retries, backoff=args.retry_backoff, stats=stats)
        # finalize stats
        stats.finish(total_items=len(items))

        target_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
        slug = iso_week_slug(target_date)
        date_str = dt.datetime.combine(target_date, dt.time.min).strftime("%Y-%m-%d")
        title = args.title or f"开源科技周报 {slug}"

        md = build_markdown(items, title, date_str, draft=args.draft)

        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_root = os.path.dirname(cwd)
        out_dir = os.path.join(cwd, args.output)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{slug}.md")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        log(f"Wrote weekly: {out_path}")

        # record new items to cache
        for it in items:
            try:
                if args.overwrite:
                    mark_seen_overwrite(conn, it, date_str)
                else:
                    mark_seen(conn, it, date_str)
            except Exception as e:
                log(f"WARN: failed to record item: {e}")
        conn.close()

        # reports
        if args.report:
            try:
                generate_report(repo_root, slug, items, stats, raw_htmls)
            except Exception as e:
                log(f"WARN: report generation failed: {e}")
                send_alert(args.monitor_webhook, {"type": "report_error", "slug": slug, "error": str(e)})

        # monitoring threshold
        if stats.failures >= max(1, len(SOURCES)//3):
            send_alert(args.monitor_webhook, {"type": "source_failures", "count": stats.failures, "total_sources": len(SOURCES)})

    if args.daemon:
        log("Daemon mode enabled: running continuously")
        while True:
            try:
                one_round()
            except Exception as e:
                log(f"ERROR: run failed: {e}\n{traceback.format_exc()}")
                send_alert(args.monitor_webhook, {"type": "fatal", "error": str(e)})
            time.sleep(max(10, args.interval))
    else:
        one_round()

# Extend CLI for HN-only segment update

# removed premature __main__ entry

# --- HN helpers (early definitions to satisfy __main__ position) ---
TECH_KEYWORDS = [
    "AI", "ML", "Python", "Go", "Rust", "JavaScript", "TypeScript",
    "Docker", "Kubernetes", "Linux", "Unix", "WebAssembly", "Wasm",
    "PostgreSQL", "SQLite", "MySQL", "Redis", "Nginx", "Apache",
    "Cloudflare", "OpenAI", "LLM", "Transformer", "GraphQL", "gRPC",
]

def standardize_title(title: str) -> str:
    t = norm_space(title)
    def title_case(s: str) -> str:
        parts = re.split(r"(\s+)", s)
        out = []
        for i, p in enumerate(parts):
            if i % 2 == 1:
                out.append(p)
                continue
            if p.isupper() and len(p) <= 4:
                out.append(p)
            elif re.match(r"^[A-Za-z].*", p):
                out.append(p.capitalize())
            else:
                out.append(p)
        return "".join(out)
    return title_case(t)

def generate_summary(summary: str) -> str:
    s = norm_space(summary)
    return s if len(s) <= 100 else (s[:97] + "...")

def highlight_keywords(text: str) -> str:
    out = text
    for kw in TECH_KEYWORDS:
        pat = re.compile(r"\b" + re.escape(kw) + r"\b", flags=re.IGNORECASE)
        out = pat.sub(lambda m: f"`{m.group(0)}`", out)
    return out

def optimize_items(items: list[Item]) -> list[Item]:
    out: list[Item] = []
    for it in items:
        t = highlight_keywords(standardize_title(it.get("title", "")))
        s = highlight_keywords(generate_summary(it.get("summary", "")))
        out.append({"title": t, "url": it.get("url", ""), "source": it.get("source", ""), "summary": s})
    return out

def parse_hn_front(html: str) -> list[Item]:
    soup = make_soup(html)
    items: list[Item] = []
    for athing in soup.select("tr.athing"):
        title_a = athing.select_one("span.titleline a")
        if not title_a:
            continue
        title = norm_space(title_a.get_text())
        url = title_a.get("href") or ""
        subtext_td = athing.find_next_sibling("tr")
        summary = ""
        if subtext_td:
            sub = subtext_td.select_one("td.subtext")
            if sub:
                score_el = sub.select_one("span.score")
                points = norm_space(score_el.get_text()) if score_el else ""
                user_el = sub.select_one("a.hnuser")
                author = norm_space(user_el.get_text()) if user_el else ""
                comments_text = ""
                for a in sub.select("a"):
                    if "comment" in (a.get_text() or "").lower():
                        comments_text = norm_space(a.get_text())
                        break
                parts = []
                if points:
                    parts.append(points)
                if author:
                    parts.append(f"by {author}")
                if comments_text:
                    parts.append(comments_text)
                summary = " | ".join(parts)
        items.append({"title": title, "url": url, "source": "https://news.ycombinator.com", "summary": summary})
        if len(items) >= 10:
            break
    return items

def format_item_line(it: Item) -> str:
    title_clean = (it.get("title", "").replace("[", "【").replace("]", "】"))
    url = it.get("url", "")
    summary = it.get("summary", "")
    return f"- [{title_clean}]({url})" + (f" — {summary}" if summary else "")

def update_markdown_segment(file_path: str, start_line: int, end_line: int, items: list[Item]):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    new_lines = [format_item_line(it) for it in items[: max(1, end_line - start_line + 1)]]
    while len(new_lines) < (end_line - start_line + 1):
        new_lines.append("- 暂无")
    lines[start_line - 1 : end_line] = new_lines
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
# --- end early HN helpers ---

if __name__ == "__main__":
    main()
