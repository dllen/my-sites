#!/bin/bash

# Hugo 网站构建脚本
# 使用方法: ./build-site.sh

set -e

echo "🔨 构建 Hugo 网站..."

# 检查是否在项目根目录
if [ ! -d "site" ] || [ ! -d "hugo" ]; then
    echo "❌ 错误: 请在项目根目录运行此脚本"
    echo "   当前工作目录: $(pwd)"
    echo "   需要的目录: site/, hugo/"
    exit 1
fi

# 检查 Hugo 二进制文件是否存在
if [ ! -f "hugo/hugo" ]; then
    echo "❌ 错误: Hugo 二进制文件不存在"
    echo "   请先构建 Hugo: cd hugo && go build -o hugo main.go"
    exit 1
fi

# 进入网站目录
cd site

# 检查网站配置文件是否存在
if [ ! -f "hugo.toml" ]; then
    echo "❌ 错误: 网站配置文件不存在"
    exit 1
fi

echo "📂 工作目录: $(pwd)"
echo "🏗️  开始构建网站..."

# 清理旧的构建文件
if [ -d "public" ]; then
    echo "🧹 清理旧的构建文件..."
    rm -rf public/*
fi

# 构建网站
echo "🔨 执行构建命令..."
../hugo/hugo --gc --minify

# 检查构建结果
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ 网站构建成功!"
    echo "📊 构建统计信息:"

    # 统计文件数量
    if command -v find &> /dev/null; then
        file_count=$(find public -type f | wc -l)
        echo "   文件总数: $file_count"
    fi

    if command -v du &> /dev/null; then
        site_size=$(du -sh public | cut -f1)
        echo "   网站大小: $site_size"
    fi

    echo ""
    echo "🚀 网站已准备就绪，可用于部署"
    echo "📁 静态文件位置: site/public/"
else
    echo "❌ 网站构建失败!"
    exit 1
fi
