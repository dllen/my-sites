#!/bin/bash

# Hugo 开发服务器启动脚本
# 使用方法: ./start-server.sh

set -e

echo "🚀 启动 Hugo 开发服务器..."

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
echo "🌐 启动地址: http://localhost:1313"
echo "⏹️  按 Ctrl+C 停止服务器"
echo ""

# 启动 Hugo 开发服务器
../hugo/hugo server -D --bind=0.0.0.0 --port=1313

echo "✅ 开发服务器已停止"
