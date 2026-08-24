# SPEC — gmail-mcp-mvp 可复现规格

> 目标：任何人按本文件可在干净环境复现「OAuth 认证的本地 MCP server 读取 Gmail」的全部验证结果。

## 1. 环境基线

| 项 | 值 |
| --- | --- |
| macOS | 26.x（Darwin） |
| Python | 3.13（brew `python@3.13`，uv 管理） |
| uv | ≥ 0.4 |
| mcp SDK | `mcp>=1.2,<2.0`（2.0 重构了 FastMCP API，勿升级） |
| Google SDK | `google-api-python-client>=2.100`、`google-auth>=2.20`、`google-auth-oauthlib>=1.2` |
| Hermes | v0.20.5（`hermes mcp add` 支持 `--command/--args` stdio 注册） |

## 2. 复现步骤

1. `cd quest-mvp-lab/gmail-mcp-mvp && uv sync`
2. `uv run pytest` → **期望 14 passed**（详见 §4 测试矩阵）
3. 无 token 冒烟：`.venv/bin/python gmail_mcp_server.py </dev/null` → 期望 stderr 输出
   `gmail-mcp: No OAuth token at .../secrets/token.json. Run uv run python setup_oauth.py first`，退出码 2。
4. Google Cloud 控制台：启用 Gmail API → OAuth consent screen（External + `gmail.readonly` scope + 测试用户）→ 创建 **Desktop** OAuth client → 下载 JSON 到 `secrets/client_secret.json`。
5. `uv run python setup_oauth.py` → 浏览器授权 → `secrets/token.json` 生成（含 refresh_token）。
6. `hermes mcp add gmail --command <abs .venv/bin/python> --args <abs gmail_mcp_server.py>` → `hermes mcp test gmail` 列出 3 工具。
7. 新会话中调用 `mcp_gmail_list_labels` / `mcp_gmail_search_threads` / `mcp_gmail_get_thread`，验证真实邮箱数据。

## 3. 架构与数据流

```
Hermes Agent (MCP client)
  └─ stdio JSON-RPC (initialize / tools/list / tools/call)
       └─ gmail_mcp_server.py (FastMCP, mcp<2)
            ├─ 工具层: list_labels / search_threads / get_thread（返回 {"labels"|"threads"|"thread"} 信封）
            └─ gmail_client.py
                 ├─ 纯函数: decode_body(优先 text/plain→text/html), format_message, format_thread
                 └─ GmailClient: users().labels().list / users().threads().list|get
                      └─ googleapiclient build("gmail","v1", credentials=OAuth token)
                           └─ gmail.googleapis.com (GA, gmail.readonly)
```

- OAuth：Desktop client + loopback（`run_local_server(port=0)`），`prompt=consent` 强制每次重新授权。
- token 生命周期：`secrets/token.json`，过期自动刷新（`build_service` 内 refresh + 写回）。
- 测试模式：`GMAIL_MCP_TEST=1` 时注入 `tests/fake_gmail.py` 的 FakeGmailClient，零网络、零 token。

## 4. 测试矩阵（14 项）

| 层 | 用例 | 验证点 |
| --- | --- | --- |
| decode_body | multipart 优先纯文本 | 返回 text/plain 内容 |
| decode_body | 仅 HTML 降级 | 返回 text/html 内容 |
| decode_body | 空 payload / 坏 base64 | 安全返回空串，不抛异常 |
| decode_body | 超长正文 | 截断到 100KB |
| format_message | 头部抽取 | subject/from/date/labelIds；默认无 body |
| format_message | include_body | body 字段出现 |
| format_thread | 消息数组整形 | id + messages 数量 |
| GmailClient | list_labels | 返回 3 个标签完整字段 |
| GmailClient | search_threads | 摘要无 body；query 透传；max_results 上限 20 |
| GmailClient | get_thread | metadata vs full 差异 |
| MCP 协议 | tools/list | 恰好 3 个工具、schema 含可选参数 |
| MCP 协议 | tools/call × 3 | list_labels / search_threads / get_thread 信封往返 |
| MCP 协议 | 未知工具 | isError=true，协议级干净报错 |
| 启动路径 | 无 token | 一行清晰报错 + 退出码 2 |

## 5. 已知边界

- Testing 态 refresh token ~7 天过期 → 重跑 `setup_oauth.py`（README 已注明）。
- FastMCP（mcp 1.x）把 list 返回值拆成逐元素 text content；工具统一返回 `{"key": [...]}` 信封规避歧义（协议测试有回归保护）。
- `mcp` 2.0 已重构 API，本项目锁 `<2.0`；升级需先验证 FastMCP 导入路径与信封行为。
- 官方 Gmail MCP GA 后：仅需改 Hermes 注册为 HTTP + OAuth client（预注册 Web client + 固定回调 URI），工具名不变。
