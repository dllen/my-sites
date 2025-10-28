#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X(Twitter) 图片抓取 + 哲思文字配对，生成一条图文信息。

特性：
- 支持多个 X 用户（用户名手工配置或通过 CLI 参数）
- 读取本地书籍文本（每行一条短句）随机挑选哲思文字
- 下载一张图片并生成 Markdown 图文文件

依赖：snscrape、requests

用法示例：
  python tech-weekly/scripts/x_image_agent.py \
    --users "naval,HN,OpenAI" \
    --output "tech-weekly/scripts/output" \
    --max-per-user 30 \
    --prefer-book "道德经"

书籍文本位置（相对仓库根目录）：
  tech-weekly/data/philosophy/
    - 纳瓦尔宝典.txt
    - 宝贵的人生建议.txt
    - 论语.txt
    - 道德经.txt
    - 诗经.txt
    - 周国平语录.txt
    - 拉罗什富科·道德箴言录.txt

每个文件按行存放短句，脚本会随机挑选长度合适的一句。
"""

import os
import re
import sys
import argparse
import random
import datetime as dt
from typing import List, Dict

import requests
import json

try:
    import snscrape.modules.twitter as sntwitter
except Exception as e:
    print("ERROR: 需要安装 snscrape。请运行: pip install snscrape", file=sys.stderr)
    raise

try:
    import yaml  # PyYAML
except Exception:
    yaml = None  # 未安装时可仅使用 JSON 配置


# ---------------------------- helpers ----------------------------

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36 "
    "XImageAgent/1.0"
)
HEADERS = {"User-Agent": UA}

# LLM Providers
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"


def repo_root() -> str:
    # scripts/ -> tech-weekly/scripts
    cwd = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(cwd))


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def log(msg: str):
    print(msg)


# ---------------------------- images from X ----------------------------

def fetch_photo_items_for_user(username: str, max_per_user: int) -> List[Dict]:
    """使用 snscrape 抓取某用户近期含图片的推文。

    返回结构：[{username, tweet_url, photo_url}]
    """
    items = []
    try:
        scraper = sntwitter.TwitterUserScraper(username)
        for i, tweet in enumerate(scraper.get_items()):
            if i >= max_per_user:
                break
            media = getattr(tweet, "media", None)
            if not media:
                continue
            # 仅取 Photo 类型
            photos = [m for m in media if m.__class__.__name__.lower() == "photo"]
            for p in photos:
                photo_url = getattr(p, "fullUrl", None) or getattr(p, "url", None)
                if not photo_url:
                    continue
                items.append({
                    "username": username,
                    "tweet_url": getattr(tweet, "url", ""),
                    "photo_url": photo_url,
                })
    except Exception as e:
        log(f"WARN: 抓取用户 @{username} 失败: {e}")
    return items


def fetch_photos(users: List[str], max_per_user: int) -> List[Dict]:
    all_items: List[Dict] = []
    for u in users:
        u = norm_space(u)
        if not u:
            continue
        log(f"抓取 @{u} 的图片推文…")
        items = fetch_photo_items_for_user(u, max_per_user)
        all_items.extend(items)
    # 去重（按 photo_url）
    seen = set()
    deduped = []
    for it in all_items:
        k = it.get("photo_url", "")
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(it)
    return deduped


def download_image(url: str, out_path: str, timeout: int = 20) -> bool:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        log(f"WARN: 下载图片失败 {url}: {e}")
        return False


# ---------------------------- quotes from books ----------------------------

BOOK_FILES = {
    "纳瓦尔宝典": "纳瓦尔宝典.txt",
    "宝贵的人生建议": "宝贵的人生建议.txt",
    "论语": "论语.txt",
    "道德经": "道德经.txt",
    "诗经": "诗经.txt",
    "周国平语录": "周国平语录.txt",
    "拉罗什富科·道德箴言录": "拉罗什富科·道德箴言录.txt",
}


def load_quotes(data_dir: str) -> Dict[str, List[str]]:
    quotes: Dict[str, List[str]] = {}
    for book, fname in BOOK_FILES.items():
        path = os.path.join(data_dir, fname)
        if not os.path.exists(path):
            log(f"INFO: 缺少书籍文件 {path}，跳过该来源")
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [norm_space(x) for x in f.read().splitlines()]
                lines = [x for x in lines if x]
                if lines:
                    quotes[book] = lines
        except Exception as e:
            log(f"WARN: 读取 {path} 失败: {e}")
    return quotes


def pick_quote(quotes_by_book: Dict[str, List[str]], prefer_book: str | None = None) -> Dict:
    """从所有书籍中挑选一条合适长度的短句。"""
    candidates = []
    books_order = list(quotes_by_book.keys())
    # 首选某本书
    if prefer_book and prefer_book in quotes_by_book:
        books_order = [prefer_book] + [b for b in books_order if b != prefer_book]
    for book in books_order:
        for q in quotes_by_book.get(book, []):
            ln = len(q)
            if 12 <= ln <= 120:
                candidates.append({"book": book, "text": q})
    if not candidates:
        # 回退：任意一句
        for book in books_order:
            for q in quotes_by_book.get(book, []):
                candidates.append({"book": book, "text": q})
    if not candidates:
        return {"book": "", "text": ""}
    return random.choice(candidates)


# ---------------------------- LLM generation ----------------------------

def build_llm_prompt(book: str, context_lines: List[str]) -> Dict[str, str]:
    """构造大模型提示词，要求生成中文哲思短句，主题受限于指定书籍。

    返回：{"system": ..., "user": ...}
    """
    examples = "\n".join([f"- {ln}" for ln in context_lines[:6]]) if context_lines else "(无示例)"
    system = (
        "你是一位中文哲思文字创作助手。请以经典书籍的思想内核为灵感，"
        "生成 1-2 句中文短句，简洁、含义清晰、富启发性。避免陈词滥调与空话。"
    )
    user = (
        f"仅以《{book}》的思想范围为主题进行创作。\n"
        f"风格与表达应与该书的思想气质相协调。\n"
        f"输出要求：\n"
        f"- 中文，优雅简洁，字数建议 20~100 字；\n"
        f"- 不要复述示例原文，不要逐字引用，进行凝练与再表达；\n"
        f"- 只返回短句正文，不要额外解释或加前后缀。\n"
        f"参考示例（节选）：\n{examples}"
    )
    return {"system": system, "user": user}


def llm_deepseek(prompt: Dict[str, str], api_key: str, temperature: float, max_tokens: int) -> str:
    if not api_key:
        return ""
    try:
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return norm_space(content)
    except Exception as e:
        log(f"WARN: DeepSeek 生成失败: {e}")
        return ""


def llm_qwen(prompt: Dict[str, str], api_key: str, temperature: float, max_tokens: int) -> str:
    if not api_key:
        return ""
    try:
        payload = {
            "model": "qwen-plus",
            "input": {
                "messages": [
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["user"]},
                ]
            },
            "parameters": {"temperature": float(temperature), "max_tokens": int(max_tokens)},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = requests.post(DASHSCOPE_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # DashScope 返回结构：{output: {text: "..."}} 或 chat：choices -> message -> content
        content = (
            data.get("output", {}).get("text")
            or (data.get("choices", [{}])[0].get("message", {}).get("content"))
            or ""
        )
        return norm_space(content)
    except Exception as e:
        log(f"WARN: 通义千问生成失败: {e}")
        return ""


def pick_quote_llm(quotes_by_book: Dict[str, List[str]], prefer_book: str | None, provider: str, temperature: float, max_tokens: int) -> Dict:
    # 选择书籍
    books = list(quotes_by_book.keys())
    if not books:
        return {"book": "", "text": ""}
    if prefer_book and prefer_book in quotes_by_book:
        book = prefer_book
    else:
        book = random.choice(books)

    context_lines = quotes_by_book.get(book, [])
    prompt = build_llm_prompt(book, context_lines)

    text = ""
    if provider.lower() == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        text = llm_deepseek(prompt, api_key, temperature, max_tokens)
    elif provider.lower() in ("qwen", "dashscope", "tongyi"):
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        text = llm_qwen(prompt, api_key, temperature, max_tokens)
    else:
        log(f"WARN: 未知 LLM 提供方 {provider}，回退到本地短句")

    if not text:
        # 回退到本地随机短句
        return pick_quote(quotes_by_book, prefer_book)
    return {"book": book, "text": text}


# ---------------------------- compose markdown ----------------------------

def compose_markdown(title: str, date_str: str, username: str, tweet_url: str, quote: Dict, image_name: str) -> str:
    fm = [
        "---",
        f"title: \"{title}\"",
        f"date: {date_str}",
        "draft: false",
        "tags: [\"AI-Agent\",\"X\",\"图片\"]",
        "categories: [\"MicroPost\"]",
        f"summary: \"来自 @{username} 的图片，配以哲思短句\"",
        "---",
        "",
    ]
    body = [
        f"来源：[@{username}]({tweet_url})",
        "",
        f"![image]({image_name})",
        "",
        f"> {quote.get('text','')}",
        f"—— {quote.get('book','')}",
        "",
    ]
    return "\n".join(fm + body)


# ---------------------------- config ----------------------------

def default_config_path() -> str:
    return os.path.join(repo_root(), "tech-weekly", "data", "x_agent.yml")


def load_config(path: str | None) -> Dict:
    """加载 YAML 或 JSON 配置文件。

    支持键：users(list[str]), prefer_book(str), use_llm(bool), llm_provider(str),
           llm_temperature(float), llm_max_tokens(int), max_per_user(int), output(str)
    """
    cfg: Dict = {}
    final_path = path or default_config_path()
    if not final_path or not os.path.exists(final_path):
        return cfg
    try:
        ext = os.path.splitext(final_path)[1].lower()
        with open(final_path, "r", encoding="utf-8") as f:
            content = f.read()
            if ext in (".yml", ".yaml") and yaml is not None:
                cfg = yaml.safe_load(content) or {}
            elif ext == ".json":
                cfg = json.loads(content)
            else:
                # 尝试 YAML，然后回退 JSON
                if yaml is not None:
                    try:
                        cfg = yaml.safe_load(content) or {}
                    except Exception:
                        cfg = json.loads(content)
                else:
                    cfg = json.loads(content)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception as e:
        log(f"WARN: 加载配置失败 {final_path}: {e}")
        cfg = {}
    return cfg


# ---------------------------- main ----------------------------

def main():
    ap = argparse.ArgumentParser(description="从 X 下载图片并配哲思文字，生成一条图文信息")
    ap.add_argument("--users", type=str, default="", help="逗号分隔的 X 用户名，例如 'naval,OpenAI'")
    ap.add_argument("--max-per-user", type=int, default=30, help="每个用户最多扫描推文数量")
    ap.add_argument("--output", type=str, default="tech-weekly/scripts/output", help="输出目录（相对或绝对路径）")
    ap.add_argument("--prefer-book", type=str, default="", help="优先选择的书籍名（可选）")
    ap.add_argument("--config", type=str, default="", help="配置文件路径（YAML 或 JSON），例如 tech-weekly/data/x_agent.yml")
    # LLM 参数
    ap.add_argument("--use-llm", type=lambda x: x.lower() == "true", default=False, help="使用大模型生成哲思短句（默认 false）")
    ap.add_argument("--llm-provider", type=str, default="deepseek", help="LLM 提供方：deepseek|qwen")
    ap.add_argument("--llm-temperature", type=float, default=0.7, help="LLM 采样温度")
    ap.add_argument("--llm-max-tokens", type=int, default=128, help="LLM 最大生成长度")
    args = ap.parse_args()
    # 加载配置并融合 CLI 参数（CLI 优先）
    cfg = load_config(args.config or None)
    # 用户列表
    if args.users:
        users = [u.strip() for u in args.users.split(",") if u.strip()]
    else:
        users = [u.strip() for u in (cfg.get("users") or []) if isinstance(u, str) and u.strip()]
    if not users:
        print("ERROR: 未提供用户列表。使用 --users 或 --config 指定。", file=sys.stderr)
        sys.exit(2)

    root = repo_root()
    data_dir = os.path.join(root, "tech-weekly", "data", "philosophy")
    quotes_by_book = load_quotes(data_dir)
    if not quotes_by_book:
        log("WARN: 未找到任何书籍短句文件，将生成无配文的图文（仅图片与来源）")

    # 其他参数融合
    max_per_user = args.max_per_user if args.max_per_user != 30 else int(cfg.get("max_per_user", 30))
    output_dir = args.output if args.output != "tech-weekly/scripts/output" else (cfg.get("output") or args.output)
    prefer_book = args.prefer_book or str(cfg.get("prefer_book", ""))
    use_llm = args.use_llm or bool(cfg.get("use_llm", False))
    llm_provider = str(cfg.get("llm_provider", args.llm_provider))
    llm_temperature = args.llm_temperature if args.llm_temperature != 0.7 else float(cfg.get("llm_temperature", 0.7))
    llm_max_tokens = args.llm_max_tokens if args.llm_max_tokens != 128 else int(cfg.get("llm_max_tokens", 128))

    # 抓取图片
    items = fetch_photos(users, max_per_user)
    if not items:
        print("ERROR: 未抓取到任何图片。请检查用户是否存在或是否有图片推文。", file=sys.stderr)
        sys.exit(1)

    # 选一张图片
    chosen = random.choice(items)
    username = chosen.get("username", "")
    tweet_url = chosen.get("tweet_url", "")
    photo_url = chosen.get("photo_url", "")

    # 选一句哲思：LLM 或本地
    if use_llm:
        quote = pick_quote_llm(
            quotes_by_book,
            prefer_book=prefer_book or None,
            provider=llm_provider,
            temperature=llm_temperature,
            max_tokens=llm_max_tokens,
        )
    else:
        quote = pick_quote(quotes_by_book, prefer_book=prefer_book or None)

    # 输出目录
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(os.path.abspath(output_dir), f"x-agent-{ts}")
    os.makedirs(out_dir, exist_ok=True)
    img_name = "image.jpg"
    img_path = os.path.join(out_dir, img_name)

    ok = download_image(photo_url, img_path)
    if not ok:
        print("ERROR: 图片下载失败，无法生成图文。", file=sys.stderr)
        sys.exit(1)

    # 生成 Markdown
    date_str = dt.datetime.now().strftime("%Y-%m-%d")
    title = f"图文：@{username} + {quote.get('book','哲思')}"
    md = compose_markdown(title, date_str, username, tweet_url, quote, img_name)
    md_path = os.path.join(out_dir, "post.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    log(f"生成成功：{md_path} (图片: {img_path})")


if __name__ == "__main__":
    main()