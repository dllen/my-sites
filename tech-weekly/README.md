# 开源科技周报（Hugo）

一个使用 Hugo 构建的简约科技风周报网站，用于聚合与整理科技与开源软件动态。

## 本地开发

- 安装依赖：已使用 Homebrew 安装 `hugo`。
- 启动开发服务器：
  - `hugo server -D`
 - 打开浏览器访问 `http://localhost:1313/`

## 内容结构

- 周报栏目：`content/weekly/`
- 静态资源：`static/`（例如图片 `static/images/2025-10-20/...`）
- 主题：`themes/tech-weekly/`
- 站点配置：`hugo.toml`

## AI 图文生成（X 图片 + 哲思短句）

- 脚本：`tech-weekly/scripts/x_image_agent.py`
- 书籍短句：`tech-weekly/data/philosophy/` 目录下每本书一个 `.txt` 文件（每行一句）
- 依赖安装：
  - `pip install -r tech-weekly/scripts/requirements.txt`
- 使用示例：
  - 本地短句：
    - `python tech-weekly/scripts/x_image_agent.py --users "naval,OpenAI" --output tech-weekly/scripts/output --max-per-user 30 --prefer-book "道德经"`
  - 使用大模型（DeepSeek 或 通义千问）生成：
    - 先设置环境变量：
      - `export DEEPSEEK_API_KEY=your_key_here`（DeepSeek）
      - 或 `export DASHSCOPE_API_KEY=your_key_here`（通义千问 / DashScope）
    - 运行：
      - `python tech-weekly/scripts/x_image_agent.py --users "naval,OpenAI" --use-llm true --llm-provider deepseek --prefer-book "道德经"`
      - 或：`python tech-weekly/scripts/x_image_agent.py --users "naval,OpenAI" --use-llm true --llm-provider qwen --prefer-book "论语"`
  - 使用配置文件（推荐 YAML）:
    - 编辑 `tech-weekly/data/x_agent.yml`（示例已提供）：
      ```yaml
      users:
        - naval
        - OpenAI
        - HN
      prefer_book: 道德经
      use_llm: true
      llm_provider: deepseek
      llm_temperature: 0.7
      llm_max_tokens: 128
      max_per_user: 30
      output: tech-weekly/scripts/output
      ```
    - 运行：
      - `python tech-weekly/scripts/x_image_agent.py --config tech-weekly/data/x_agent.yml`
- 输出：
  - 在 `tech-weekly/scripts/output/x-agent-YYYYMMDD-HHMMSS/` 生成 `image.jpg` 与 `post.md`

> 注：脚本默认优先从本地七本书中挑选短句。如需补充或替换内容，请将短句按行写入对应书籍文件。
> 若启用大模型生成（`--use-llm true`），将以指定书籍的本地短句为参考上下文，生成 1-2 句新的哲思文字；当模型不可用或失败时自动回退到本地短句。

## 新增一期开周报（SOP）

1. 新建内容文件（推荐使用日期作为文件名）：
   - `hugo new weekly/YYYY-MM-DD.md`
   - 若命令生成的默认内容不符合模板，可复制上一期文件或使用 `archetypes/weekly.md` 作为参考。
2. 编辑 Front Matter：
   - `title`: 如 `第 01 期 | 开源科技周报`
   - `date`: 例如 `2025-10-20`
   - `draft`: 发布前设为 `false`
   - `tags`: 建议包含 `周报`, `开源`, `科技`
   - `categories`: 建议包含 `Weekly`
   - `summary`: 1 行简述本期亮点
3. 填充正文四大版块（建议）：
   - `趋势观察`: 行业趋势、值得关注的方向
   - `开源项目`: 新项目或值得收藏的仓库
   - `新版本发布`: 重要软件/框架版本更新
   - `阅读推荐`: 高质量文章或报告
4. 本地预览：
   - 运行 `hugo server -D`，浏览器查看首页与详情页
   - 检查链接有效性、摘要长度与排版
5. 发布上线（可选）：
   - 构建静态文件：`hugo --minify`
   - 将 `public/` 部署至你的静态托管平台（例如 GitHub Pages、Vercel、Netlify）

## 数据收集建议（人工聚合 SOP）

- 每周固定时间窗口收集与筛选：
  - `GitHub Trending` / `Awesome` 列表
  - 开源项目 Release Notes（例如 Kubernetes、Rust、Vite、Next.js 等）
  - 社区热点：`Hacker News`、`Lobsters`、`Reddit r/programming`
  - 厂商技术博客与安全公告
- 选题标准：
  - 具有实际影响或学习价值、清晰的发布信息、可信来源
  - 对读者可操作（代码、案例、文章）
- 编写规范：
  - 每条目提供清晰的标题、链接与 1 句简述
  - 英文原文优先，必要时补充中文解读
  - 避免冗长，控制每条目 1–2 行

## 分类与标签建议

- `categories`: 使用 `Weekly` 统一分类
- `tags`: 可按主题添加，如 `AI`, `Rust`, `Web`, `Security`, `DevOps`

## 目录约定（建议）

- 图片：`static/images/<YYYY-MM-DD>/<slug>.png`
- 附件：`static/files/<YYYY-MM-DD>/<slug>.pdf`

## 质量检查清单

- 链接均可访问且正确跳转
- 摘要不超过 140 字，避免过长
- 标签与分类已添加，便于检索
- 页面预览无样式或排版异常

## 后续增强（可选）

- 通过 `data/` 目录维护来源清单，结合短代码批量渲染
- 接入 RSS 抓取与缓存（Hugo 支持 `getJSON`/`resources.GetRemote`）
- 主题增加亮色与暗色切换