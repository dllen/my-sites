#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tech Weekly aggregator: fetch multiple sources and generate a Hugo weekly markdown.

Usage:
  python scripts/fetch_weekly.py --output content/weekly --max-per-source 20 --timeout 15 --draft false
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

import requests
from bs4 import BeautifulSoup
import feedparser
import sqlite3

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


def run(max_per_source: int, timeout: int, sleep: float, conn: sqlite3.Connection) -> list[Item]:
    all_items: list[Item] = []
    for url, parser in SOURCES:
        try:
            html = fetch(url, timeout=timeout)
            if parser:
                items = parser(html)
            else:
                # try simple RSS first if the URL looks like a feed (rare here), else generic
                items = parse_generic(url, html, picks=max_per_source*2)
            # trim
            items = items[:max_per_source]
            # filter out items already seen across runs
            fresh: list[Item] = []
            for it in items:
                if not has_seen(conn, it.get("url", "")):
                    fresh.append(it)
            all_items.extend(fresh)
            time.sleep(sleep)
        except Exception as e:
            log(f"WARN: failed {url}: {e}")
            continue
    return dedup(all_items)


def main():
    ap = argparse.ArgumentParser(description="Fetch sources and generate weekly markdown")
    ap.add_argument("--output", default="content/weekly", help="Output directory under tech-weekly")
    ap.add_argument("--max-per-source", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--sleep", type=float, default=0.8, help="sleep seconds between sources")
    ap.add_argument("--draft", type=lambda x: x.lower() == "true", default=False)
    ap.add_argument("--date", type=str, help="YYYY-MM-DD weekly date")
    ap.add_argument("--title", type=str, default=None, help="Custom title")
    args = ap.parse_args()

    # open cache db
    conn = get_db()

    # aggregate (skip already seen)
    items = run(args.max_per_source, args.timeout, args.sleep, conn)
    # compute date/slug/title from provided date
    # today = dt.date.today()
    # slug = iso_week_slug(today)
    # date_str = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    # title = f"开源科技周报 {slug}"
    target_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    slug = iso_week_slug(target_date)
    date_str = dt.datetime.combine(target_date, dt.time.min).strftime("%Y-%m-%d")
    title = args.title or f"开源科技周报 {slug}"

    md = build_markdown(items, title, date_str, draft=args.draft)

    # resolve output path relative to repo root
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(cwd, args.output)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{slug}.md")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    log(f"Wrote weekly: {out_path}")

    # record new items to cache
    for it in items:
        try:
            mark_seen(conn, it, date_str)
        except Exception as e:
            log(f"WARN: failed to record item: {e}")

    conn.close()


if __name__ == "__main__":
    main()