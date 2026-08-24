# gmail-mcp-mvp

OAuth 认证的本地 MCP server，通过 **GA 版 Gmail REST API** 读取 Gmail —— 无 Google Developer Preview 门槛，工具名镜像官方 Gmail MCP，Hermes Agent 一键接入。

```
Hermes Agent ──stdio MCP──▶ gmail_mcp_server.py ──OAuth 2.0(REST)──▶ gmail.googleapis.com
                                 │
                                 └─ secrets/token.json（本地缓存，自动刷新）
```

## 背景

[Scalekit: Gmail MCP vs Gmail API](https://www.scalekit.com/blog/gmail-mcp-vs-api) 的核心结论：

- 官方 Gmail MCP（`gmailmcp.googleapis.com/mcp/v1`）处于 **Workspace Developer Preview**，需项目注册 preview 计划；OAuth client 必须是预注册 Web 类型且回调 URI 由 Google 固定（`claude.ai/api/mcp/auth_callback` 等），只支持交互式 OAuth，**无服务账号路径**；10 个工具、不能发信。
- Gmail REST API 是 GA 接口，全量能力，OAuth / 服务账号 / JWT 多路径。

本 MVP 验证的是「读 Gmail」这条最小链路，选型对比见下表。

## 关键决策

| 维度 | 官方 Gmail MCP | 本 MVP（自建 stdio server） |
| --- | --- | --- |
| 可用性 | Developer Preview 门槛（项目注册 + 固定回调 URI） | GA Gmail API，零门槛 |
| OAuth 客户端 | 预注册 Web client（需控制台配 Google 固定回调） | Desktop client，loopback 回调，Hermes 兼容 |
| 工具面 | 10 个（读 + 草稿 + 标签操作） | 只读 3 个：`list_labels` / `search_threads` / `get_thread` |
| 权限边界 | `gmail.readonly` + `gmail.compose` | **仅 `gmail.readonly`**（最小权限） |
| 发信能力 | 仅草稿，不可直发 | 无（MVP 不做） |
| 数据落地 | token 存客户端本地 | token 存项目 `secrets/`（gitignored） |
| 工具名兼容 | — | **镜像官方命名**，将来切官方 server 无需改 prompt |

## 能力边界

| 工具 | 功能 | 边界 |
| --- | --- | --- |
| `list_labels` | 列出标签（id/name/type） | 只读 |
| `search_threads(query, max_results)` | Gmail 语法搜索线程，返回摘要（主题/发件人/日期/snippet/标签） | 不含正文；`max_results` 上限 20 |
| `get_thread(thread_id, include_body)` | 按 ID 取线程全文；`include_body=true` 返回解码正文（优先纯文本，降级 HTML） | 单消息正文上限 100KB；`metadata` 模式不含正文 |

**明确不做**：发信 / 草稿 / 标签变更 / 附件下载 / 服务账号 —— 保持最小只读面，规避敏感 scope 扩展。

## 快速开始

### 0. 环境

macOS + Python 3.11+（本项目用 `uv` + python3.13）。

```bash
cd quest-mvp-lab/gmail-mcp-mvp
uv sync          # 安装依赖（.venv）
```

### 1. 创建 Google Cloud OAuth client（一次性，约 3 分钟）

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)（用你的 Google 账号），新建项目或复用现有项目。
2. **APIs & Services → Library**，搜索并启用 **Gmail API**（`gmail.googleapis.com`）。
3. **APIs & Services → OAuth consent screen**：User type 选 **External**，必填项填应用名和邮箱；**Scopes** 里添加 `https://www.googleapis.com/auth/gmail.readonly`；**Test users** 里添加你自己的 Gmail 地址。
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**，类型选 **Desktop app**，创建后点击 **Download JSON**，保存为 `secrets/client_secret.json`。

> 测试态说明：未发布（Testing）状态下 refresh token 约 7 天过期，到期重跑 `setup_oauth.py` 即可；若发布为 Production 并点击通过「未验证应用」警告，token 长期有效，但 console 会提示受限 scope 需验证——个人自用无需理会。

### 2. 授权（唯一的人工步骤）

```bash
uv run python setup_oauth.py
```

浏览器自动打开 → 选择你的 Gmail 账号 → 授权 → 自动写回 `secrets/token.json`。

### 3. 注册到 Hermes

```bash
hermes mcp add gmail \
  --command /Users/nanzhi/workspace/quest-mvp-lab/gmail-mcp-mvp/.venv/bin/python \
  --args /Users/nanzhi/workspace/quest-mvp-lab/gmail-mcp-mvp/gmail_mcp_server.py
```

### 4. 验证

```bash
hermes mcp test gmail          # 应列出 3 个工具
```

新开一个会话（MCP 工具在会话启动时注册），直接说「看看我的 Gmail 最近邮件」「搜索来自 Alice 的邮件」即可。工具名前缀 `mcp_gmail_*`。

## 测试

```bash
uv run pytest          # 14 个测试：转换函数单测 + MCP wire 协议全链路（无需真实 token）
```

协议测试用 `GMAIL_MCP_TEST=1` 注入内存 fake Gmail 服务，覆盖：工具清单、参数 schema、三次工具调用往返、未知工具报错。

## 结论

- **OAuth + MCP 读 Gmail 的最小闭环成立**：Desktop client + loopback 授权 + 本地 token 缓存 + stdio MCP，全程无 Developer Preview 依赖，14/14 测试通过。
- 官方 Gmail MCP 的价值在「托管 + 免运维」，但 preview 门槛与固定回调 URI 使其不适合个人快速自用；本方案把 Gmail 能力收敛为只读 3 工具，权限面最小。
- 工具名与官方对齐，未来 Google 开放 GA 后，把 server 换成 `gmailmcp.googleapis.com` 即可，prompt 无需改动。

## 参考

- [Scalekit: Gmail MCP vs Gmail API](https://www.scalekit.com/blog/gmail-mcp-vs-api)
- [Google: Configure the Gmail MCP server](https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server)
- [Google: Gmail MCP reference (工具清单)](https://developers.google.com/workspace/gmail/api/reference/mcp)
- [tiny-gmail-mcp（同思路的 Node 实现）](https://github.com/mitchfitzsimmons/tiny-gmail-mcp)
- [Hermes: native-mcp skill](https://hermes-agent.nousresearch.com/docs)
