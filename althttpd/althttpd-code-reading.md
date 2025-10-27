# althttpd 源码解读（基于 `althttpd/althttpd.c`）

本文面向希望快速理解 althttpd 单文件 HTTP 服务器实现的读者，概述其设计目标、关键数据结构、核心流程与安全策略，并给出在本项目中的使用建议。

## 1. 项目定位与特性
- 单文件、极简、安全的 HTTP 服务器，便于审计与维护。
- 两种运行模式：由 inetd/stunnel 等按连接触发；或独立进程监听端口。
- 支持静态内容、CGI、SCGI；按 `Host:` 头选择虚拟站点目录（`*.website`）。
- 无配置文件；通过命令行参数控制行为；统一 CSV/CLF 日志。

## 2. 编译与运行（本目录）
- 进入 `althttpd/`，使用 `Makefile` 构建：`make`。
- 生成的可执行文件为 `althttpd/althttpd`。
- 独立模式示例：`./althttpd --port 8080 --root /path/to/webroot`。
- inetd/stunnel 模式：由上游创建连接并交给 althttpd 处理。

## 3. 关键编译期宏
- `DEFAULT_PORT`：独立模式默认监听端口，默认为 `"80"`。
- `MAX_CONTENT_LENGTH`：请求体最大长度（默认 250MB）。
- `MAX_CPU`：每个连接可用 CPU 秒数（默认 30）。
- 其他宏在不同版本中可能扩展，如 TLS 支持需额外宏与库（该目录版本未启用）。

## 4. 全局状态与环境映射
源码通过大量全局变量记录请求与服务器状态，简化参数传递：
- 请求相关：`zProtocol`、`zMethod`、`zScript`/`zRealScript`、`zQueryString`、`zFile`、`zDir`、`zPathInfo`。
- 头部信息：`zAgent`、`zHttpHost`、`zReferer`、`zAccept`、`zAcceptEncoding`、`zContentLength`、`zContentType`、`zIfNoneMatch`、`zIfModifiedSince`。
- 连接信息：`zRemoteAddr`、`zServerName`、`zServerPort`、`zRealPort`。
- 日志与统计：`nIn`、`nOut`、`zReplyStatus`、`beginTime`、`nRequest`、`priorSelf`、`priorChild`。
- 运行控制：`standalone`、`ipv4Only`/`ipv6Only`、`mxAge`（Cache-Control）、`useTimeout`、`closeConnection`、`maxCpu`。

环境变量映射表 `cgienv[]` 将上述信息映射到 CGI 环境中，例如：
- `CONTENT_LENGTH`、`CONTENT_TYPE`、`QUERY_STRING`、`REMOTE_ADDR`、`REQUEST_METHOD`、`REQUEST_URI`、`SCRIPT_FILENAME`、`SERVER_NAME`、`SERVER_PORT`、`SERVER_PROTOCOL` 等。

## 5. 命令行参数与运行模式
- `--root DIR`：站点根目录（含 `*.website` 子目录）。root 权限下可 chroot 至此并降权。
- `--port N`：独立模式监听端口 N。
- `--user USER`：指定降权用户（安全要求：拒绝以 root 身份处理网络输入）。
- `--logfile FILE`：按 CSV/CLF 记录日志（支持 `strftime` 展开）。
- `--https`：标记输入已被上游 SSL 解密（本版本不内置 TLS）。
- `--family ipv4|ipv6`：独立模式选择 IP 协议族。
- `--jail BOOLEAN`：是否在 root 权限下形成 chroot（默认启用）。
- `--max-age SEC`：设置 `Cache-Control: max-age`。
- `--max-cpu SEC`：每连接 CPU 时间上限（0 表示无限）。
- `--debug`：关闭输入超时，便于手工调试。

## 6. 目录结构与虚拟主机
- 根据 `Host:` 头选择 `${HTTP_HOST}.website` 子目录作为站点根；否则回退 `default.website`。
- 独立模式下若两者都不存在，可直接从进程启动目录提供内容（便捷测试）。

## 7. 路径与安全策略
- 路径组件首字符为 `.` 或 `-` 将被拒绝（404），用于避免目录穿越、隐藏敏感内容；`/.well-known/` 为例外以支持 ACME。
- 仅允许 `[0-9a-zA-Z,-./:_~]` 与 `%HH` 编码，其他字符转换为 `_`（防 XSS/路径注入）。
- 请求 URI 必须以 `/` 开始，否则 404。
- 不设置任何以 `() {` 开头的环境变量（防止 Shellshock 类漏洞）。

## 8. 请求生命周期（概要）
1) 启动与权限处理：在 root 下先 chroot 到 `--root`，降权到 `--user` 或目录所有者，再读取网络输入。
2) 解析请求：读取请求行与头部，校验 `HTTP_HOST` 与路径合法性。
3) 路由与响应：
   - 静态文件：按后缀映射 `Content-Type`；若客户端接受 gzip 且存在同名 `.gz`，优先返回并设置 `Content-Encoding: gzip`。
   - CGI：对可执行文件设置 CGI 环境并 `exec`，捕获输出返回。
   - SCGI：对 `.scgi` 规格文件解析 `SCGI hostname port`，与后端交互；支持 `relight` 重启命令与 `fallback` 文件。
4) 连接控制与范围请求：根据 `Range:` 头处理部分内容；必要时添加 `Connection: close`。
5) 日志记录与资源统计：记入 CSV/CLF 日志，包括时间、IP、URL、Referer、状态、字节数、CPU 时间、墙钟时间等。

## 9. CGI 机制
- 通过 `cgienv[]` 构建环境变量（如 `REQUEST_METHOD`、`SCRIPT_FILENAME` 等）。
- 设置工作目录、PATH 等后执行目标脚本/二进制；遵守超时与资源限制。
- 将 CGI 输出直接转发至客户端；支持基于 `-auth` 文件的基础认证与策略：`http-redirect`、`https-only`、`user NAME LOGIN:PASSWORD`、`realm TEXT`。

## 10. SCGI 机制
- `.scgi` 文件格式：
  ```
  SCGI hostname port
  fallback: fallback-filename
  relight: relight-command
  ```
- 连接失败时：若存在 `relight:`，先运行命令（可后台 `&`，建议重定向到 `/dev/null`），稍后重试；仍失败则返回 `fallback` 文件内容。

## 11. 日志系统
- 函数 `MakeLogEntry()` 负责统一日志输出。
- 两种格式：
  - 默认 CSV：包含时间、IP、URL、Referer、状态码、收发字节、子/父进程 CPU 时间、墙钟时间等。
  - 可选 CLF（`COMBINED_LOG_FORMAT` 宏）：与常见 Web 服务器兼容格式。
- 支持 `strftime` 展开日志文件名，便于按日期滚动。

## 12. 超时与资源限制
- 输入与 CGI 执行具有硬编码超时（`--debug` 可关闭输入超时）。
- `MAX_CONTENT_LENGTH` 防止大请求滥用。
- `MAX_CPU` 与每连接资源统计防止过度消耗。

## 13. 错误处理与响应细节
- 严格的 403/404 等校验与错误路径处理。
- `Range:` 请求的边界检查与部分内容返回（206）。
- 规范的头部输出与状态线管理（`zReplyStatus`、`statusSent`）。

## 14. 与本项目集成建议
- Hugo 构建产物在 `site/public/`。若希望用 althttpd 本地服务，可：
  - 创建 `webroot/default.website/` 并将 `site/public/` 内容复制或链接到其中。
  - 启动：`./althttpd --port 8080 --root /absolute/path/to/webroot`。
- 若不需要 CGI/SCGI，仅静态托管，Cloudflare Pages 已满足生产部署；althttpd 更适合本地或简易独立服务场景。

## 15. 常见问题与排查
- 权限错误：确认以非 root 身份读取网络数据；在需要时正确 chroot 并降权。
- 404/403 频发：检查路径字符与首字符规则，确认未使用被禁止的前缀或字符。
- 日志路径：`--logfile` 路径在 chroot 之后解析；确保路径在 jail 内可访问。
- SCGI 调试：确保后端存活；`relight` 命令静默运行并适当重试；`fallback` 提供友好错误页。

## 16. 代码导航（示例）
- 字符串与时间：`Escape()`、`tvms()`。
- 日志：`MakeLogEntry()`。
- 路径解析与合法性检查：相关解析函数与校验逻辑贯穿请求处理。
- CGI 环境构建：`cgienv[]` 表与环境填充代码。
- 独立模式网络：socket 创建、`bind`/`listen`/`accept`、子进程处理请求。

---

## 附录：客户端 HTTP 请求处理流程（细化）

**总体流程**
- 接收连接：独立模式监听端口并 `accept`；inetd/stunnel 模式直接从已打开的套接字读取。
- 权限与隔离：在允许的情况下 `chroot` 到 `--root` 并降权到 `--user` 或目录所有者，随后才读取网络输入。
- 读取与解析：解析请求行与头部，填充 `zMethod`、`zProtocol`、`zScript`、`zHttpHost`、`zContentLength` 等状态。
- 主机与路径解析：根据 `Host:` 选择 `${HTTP_HOST}.website` 或 `default.website`；对 URI 做合法性与安全校验。
- 路由：判断静态文件、可执行（CGI）、或 `.scgi` 规格文件（SCGI），分支处理。
- 输出响应：写状态行与头部，返回内容；必要时添加 `Connection: close`。
- 记录日志：写入 CSV/CLF，包含字节数、耗时、状态码等，并清理资源。

**解析与校验**
- 请求行：提取 `zMethod`（主要 GET）、`zProtocol`（HTTP/1.0/1.1）、`zScript`、`zQueryString`。
- 头部：`zHttpHost`、`zAgent`、`zReferer`、`zAccept`、`zAcceptEncoding`、`zContentType`、`zContentLength`、`zIfModifiedSince`、`zIfNoneMatch`。
- 主机目录：按 `${HTTP_HOST}.website` 选择目录，缺失时回退到 `default.website` 或当前工作目录（独立模式）。
- 路径安全：仅允许 `[0-9a-zA-Z,-./:_~]` 与合法 `%HH`，其他字符转换为 `_`；路径组件首字符为 `.` 或 `-` 则拒绝（除 `/.well-known/` 特例）；URI 必须以 `/` 开始。

**静态内容**
- 定位：将 `zScript` 规范化为磁盘路径，计算 `zFile`、`zDir`、`zPathInfo`；目录命中时补全 `index.html` 到 `zRealScript`。
- 类型与压缩：后缀映射 `Content-Type`；若 `Accept-Encoding: gzip` 且存在 `NAME.gz`，返回压缩文件并加 `Content-Encoding: gzip`。
- 条件与范围：处理 `If-Modified-Since`/`If-None-Match`（304）；解析并校验 `Range:`，返回 206 部分内容。

**CGI（可执行文件）**
- 识别：目标文件带执行位视为 CGI。
- 环境：用 `cgienv[]` 填充 `REQUEST_METHOD`、`SCRIPT_FILENAME`、`QUERY_STRING`、`REMOTE_ADDR`、`SERVER_NAME`、`SERVER_PORT` 等。
- 认证：同目录 `-auth` 文件启用 Basic Auth 与策略（`http-redirect`、`https-only`、`user NAME LOGIN:PASSWORD`、`realm TEXT`）。
- 执行：设置工作目录与 `PATH` 后 `exec`，转发输出；受超时与资源限制保护。

**SCGI（.scgi 文件）**
- 识别：非可执行且以 `.scgi` 结尾的文件。
- 规格：首行 `SCGI hostname port`；失败时按 `relight:`（静默重启后端）与 `fallback:`（返回备选文件）策略处理。
- 转发：建立到后端的 SCGI 连接，发送请求并回传响应。

**响应输出**
- 维护 `zReplyStatus` 与 `statusSent`，规范写出状态行与头部；按需设置 `Cache-Control: max-age` 与 `Connection: close`。

**日志与统计**
- 结束时写日志：时间、IP、URL、Referer、状态码、收/发字节、子/父进程 CPU 时间、墙钟时间等；支持 `COMBINED_LOG_FORMAT` 切换 CLF。

**错误处理**
- 403：非法 `Host:`、路径含非法字符、路径组件前缀禁用等。
- 404：目标不存在、URI 不以 `/` 开始、目录穿越尝试。
- 5xx：CGI/SCGI 执行失败或后端不可达且无 `fallback`。
- 418/封禁：结合 `--ipshun` 对明显恶意请求临时封禁 IP。

**独立模式与 inetd 差异**
- 独立模式：主进程 `bind`/`listen`，循环 `accept`，每连接 fork 子进程处理；可设置 `--family ipv4|ipv6` 与并发上限。
- inetd/stunnel：直接从已打开的套接字处理，无需监听；安全与降权流程一致。