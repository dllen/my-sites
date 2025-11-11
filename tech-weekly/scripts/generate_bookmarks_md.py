#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从 tech-weekly/data/bookmarks/bookmarks.json 读取书签结构，生成 Markdown 页面：
  - 输出到 tech-weekly/content/bookmarks.md
  - 分组：按顶层文件夹（如果有标题）分组
  - 列表项：- [标题](链接)

使用：
  python3 tech-weekly/scripts/generate_bookmarks_md.py \
    --input tech-weekly/data/bookmarks/bookmarks.json \
    --output tech-weekly/content/bookmarks.md
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def is_folder(node: Dict) -> bool:
    return isinstance(node, dict) and "children" in node and not node.get("url")


def is_link(node: Dict) -> bool:
    return isinstance(node, dict) and bool(node.get("url"))


def walk_bookmarks(node: Dict, parents: Optional[List[str]] = None) -> List[Tuple[List[str], str, str]]:
    """递归遍历书签，返回 (父级标题路径, 标题, 链接) 列表"""
    if parents is None:
        parents = []

    results: List[Tuple[List[str], str, str]] = []
    if not isinstance(node, dict):
        return results

    # 当前节点是链接
    if is_link(node):
        title = node.get("title") or node.get("name") or node.get("url")
        url = node.get("url")
        results.append((parents[:], title, url))
        return results

    # 当前节点是文件夹
    title = node.get("title")
    if title:
        parents = parents + [title]

    # 遍历 children
    children = node.get("children", [])
    for child in children:
        results.extend(walk_bookmarks(child, parents))

    return results


def collect_links(root: Dict) -> List[Tuple[List[str], str, str]]:
    links: List[Tuple[List[str], str, str]] = []
    # 顶层可能是 { bookmarks: [...] }
    if "bookmarks" in root and isinstance(root["bookmarks"], list):
        for entry in root["bookmarks"]:
            links.extend(walk_bookmarks(entry, []))
    else:
        links.extend(walk_bookmarks(root, []))
    return links


def group_by_top_folder(items: List[Tuple[List[str], str, str]]) -> Dict[str, List[Tuple[List[str], str, str]]]:
    grouped: Dict[str, List[Tuple[List[str], str, str]]] = {}
    for parents, title, url in items:
        top = parents[0] if parents else "未命名分类"
        grouped.setdefault(top, []).append((parents, title, url))
    return grouped


def build_markdown(grouped: Dict[str, List[Tuple[List[str], str, str]]], total_count: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d")
    lines: List[str] = []
    # Hugo Front Matter
    lines.append("---")
    lines.append("title: 书签列表")
    lines.append(f"date: {now}")
    lines.append("draft: false")
    lines.append("slug: bookmarks")
    lines.append("tags: [书签, 链接收藏]")
    lines.append("categories: [资料汇总]")
    lines.append("---")
    lines.append("")

    lines.append(f"共收录 {total_count} 个书签。来源：`data/bookmarks/bookmarks.json`。")
    lines.append("")

    # 固定说明
    lines.append("说明：按顶层文件夹分组展示，层级较深时仅显示链接列表。")
    lines.append("")

    # 按组输出
    for section in sorted(grouped.keys()):
        items = grouped[section]
        lines.append(f"## {section}（{len(items)}）")
        lines.append("")
        # 尝试二级分组（次级文件夹），但若层级复杂则直接输出链接
        # 二级标题 -> 链接列表
        # 检测是否存在不同的二级父
        second_levels = {}
        for parents, title, url in items:
            second = parents[1] if len(parents) > 1 else None
            second_levels.setdefault(second, []).append((parents, title, url))

        # 若只有一个None分组，则直接平铺链接
        if len(second_levels) == 1 and None in second_levels:
            for _, title, url in sorted(second_levels[None], key=lambda x: x[1].lower() if isinstance(x[1], str) else str(x[1])):
                lines.append(f"- [{title}]({url})")
            lines.append("")
            continue

        # 否则按二级文件夹分组输出
        for second in sorted([k for k in second_levels.keys() if k is not None]):
            sub_items = second_levels[second]
            lines.append(f"### {second}（{len(sub_items)}）")
            lines.append("")
            for _, title, url in sorted(sub_items, key=lambda x: x[1].lower() if isinstance(x[1], str) else str(x[1])):
                lines.append(f"- [{title}]({url})")
            lines.append("")

        # 最后输出未归入二级的链接
        if None in second_levels:
            lines.append("### 其它")
            lines.append("")
            for _, title, url in sorted(second_levels[None], key=lambda x: x[1].lower() if isinstance(x[1], str) else str(x[1])):
                lines.append(f"- [{title}]({url})")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成书签列表 Markdown 页面")
    parser.add_argument("--input", required=True, help="输入 JSON 文件路径")
    parser.add_argument("--output", required=True, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        root = load_json(input_path)
        links = collect_links(root)
        grouped = group_by_top_folder(links)
        md = build_markdown(grouped, total_count=len(links))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            f.write(md)

        print(f"已生成: {output_path} （共 {len(links)} 个链接）")
    except Exception as e:
        print(f"生成失败: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()