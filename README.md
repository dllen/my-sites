# Rocnix Site

个人网站项目，使用 Hugo + Cloudflare Pages 构建。

## 项目结构

```
rocnix-site/
├── .gitignore         # Git 忽略文件配置
├── hugo/              # Hugo 源码（submodule）
│   └── hugo          # Hugo 二进制文件
├── site/             # 网站源码
│   ├── content/      # 网站内容
│   │   ├── posts/    # 博客文章
│   │   ├── bookmarks/# 书签页面
│   │   └── about/    # 关于页面
│   ├── themes/       # 主题目录
│   ├── static/       # 静态资源
│   └── hugo.toml     # 网站配置
├── .github/
│   └── workflows/
│       └── deploy.yml # 部署工作流
├── start-server.sh   # 开发服务器启动脚本
└── build-site.sh     # 网站构建脚本
```

## 本地开发

1. 克隆项目：
```bash
git clone <repository-url>
cd rocnix-site
git submodule update --init --recursive
```

2. 构建 Hugo：
```bash
cd hugo
go build -o hugo main.go
```

3. 使用便捷脚本：

   **启动开发服务器**：
   ```bash
   ./start-server.sh
   ```

   **重新构建网站**：
   ```bash
   ./build-site.sh
   ```

   **或者使用传统命令**：
   ```bash
   cd site
   ../hugo/hugo server -D  # 启动服务器
   ../hugo/hugo --gc --minify  # 构建网站
   ```

4. 访问 `http://localhost:1313` 查看网站

## 部署

项目使用 GitHub Actions 自动部署到 Cloudflare Pages：

1. 在 Cloudflare Pages 中创建新项目
2. 连接 GitHub 仓库
3. 设置环境变量：
   - `CLOUDFLARE_API_TOKEN`: Cloudflare API 令牌
   - `CLOUDFLARE_ACCOUNT_ID`: Cloudflare 账户 ID

## 便捷脚本

项目提供了两个便捷的 shell 脚本，简化日常开发和部署操作：

### start-server.sh - 启动开发服务器

```bash
./start-server.sh
```

功能：
- 自动检查项目结构和依赖
- 启动 Hugo 开发服务器在 `http://localhost:1313`
- 显示友好的状态信息和错误提示
- 支持 Ctrl+C 优雅停止

### build-site.sh - 重新构建网站

```bash
./build-site.sh
```

功能：
- 清理旧的构建文件
- 重新构建完整的静态网站
- 显示构建统计信息（文件数量、大小等）
- 自动验证构建结果

**注意**：两个脚本都必须在项目根目录运行，且需要 Hugo 二进制文件存在。

## 主题

使用 [Hugo Bear Blog](https://github.com/janraasch/hugo-bearblog) 主题。

这是一个简洁、美观、专注于内容的主题，非常适合博客和个人网站。

## 内容管理

- **博客文章**：放在 `site/content/posts/` 目录
- **书签**：放在 `site/content/bookmarks/` 目录
- **关于页面**：`site/content/about/index.md`

## .gitignore 配置

项目包含了完整的 `.gitignore` 文件，自动忽略以下类型的文件：

- **构建产物**：`site/public/` 目录（Hugo 构建输出）
- **临时文件**：`*.tmp`、`*.log`、`*.cache` 等
- **IDE 文件**：`.vscode/`、`.idea/`、`.DS_Store` 等
- **依赖目录**：`node_modules/`、`vendor/` 等
- **系统文件**：各种操作系统生成的临时文件

这样可以确保只将必要的源码文件提交到版本控制系统中，避免污染仓库。

## 许可证

MIT License
