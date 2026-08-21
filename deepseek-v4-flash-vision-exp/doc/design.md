# DeepSeek 视觉多模态 MVP — 设计文档

## 背景与目标

DeepSeek 于 2026-08-21 发布视觉多模态模型 `deepseek-v4-flash-vision-exp`（实验性，额外接受图片输入）。
本 MVP 用本地实测图片（《欢迎来龙餐馆》漫画页 3 张）验证该模型的真实能力：

1. **图像理解**：描述画面、OCR 提取漫画文字、多图剧情连贯性对比。
2. **创作能力**：基于图片内容的剧情续写、分镜脚本创作。
3. **多模态生成能力**（自主探测）：官方文档通篇只讲图像理解，未提及图像输出。
   通过多轮针对性探测，实证模型是否具备图像/多模态内容生成能力。

同时验证两条图片输入路径：**base64 内联** 与 **Files API（上传 → file_id 引用）**，
其中 01.jpg 同时走两条路径做结果对照。

## 技术选型

| 关注点 | 选择 | 理由 |
| ------ | ---- | ---- |
| 语言 | Rust 1.96 | 用户指定；模块化/类型安全/TDD 友好 |
| HTTP | reqwest (blocking + json + multipart) | 业界标准，multipart 支持 Files API 上传 |
| JSON | serde + serde_json | 协议契约强类型化 |
| base64 | base64 crate | 标准实现 |
| 配置 | dotenvy + std::env | .env 加载，API key 不进代码 |
| CLI | clap (derive) | 场景化任务入口 |
| 错误 | anyhow | MVP 级错误处理 |
| 测试 | 单元测试 + httpmock | 协议/图片/配置纯单元测试；客户端用 mock server 集成测试，不依赖真实网络 |

## 目录结构

```
deepseek-v4-flash-vision-exp/
├── Cargo.toml            # crate: ds-vision（binary）
├── .env                  # DEEPSEEK_API_KEY=...（gitignored，0600 权限）
├── .gitignore            # 项目层：.env / 密钥 / 报告产物
├── README.md             # 背景、关键决策、构建/运行/验证、结论
├── doc/design.md         # 本文档
├── testimages/           # 本地实测图片（01.jpg / 02.webp / 03.jpg）
├── reports/              # 运行结果（gitignored，带时间戳目录）
├── src/
│   ├── main.rs           # CLI 编排：解析任务 → 执行 → 报告落盘
│   ├── lib.rs            # 根 re-export（Client、Config、任务函数）
│   ├── config.rs         # 配置加载：API key 安全读取、模型名、base_url
│   ├── image.rs          # 图片处理：magic-byte 格式探测、base64、data URL
│   ├── protocol.rs       # OpenAI 兼容协议类型：ContentBlock / ChatRequest / ChatResponse / FileObject
│   ├── client.rs         # DeepSeekClient：chat() + upload_file()，两种传图路径
│   ├── prompts.rs        # 场景提示词（描述/OCR/对比/创作/生成探测）
│   └── reporter.rs       # 结果落盘：Markdown + JSON（带时间戳）
└── tests/                # 集成测试（httpmock）
```

## 模块职责（高内聚、低耦合）

| 模块 | 职责 | 依赖 |
| ---- | ---- | ---- |
| `config` | 从 env/.env 读取 `DEEPSEEK_API_KEY`、`DS_MODEL`、`DS_BASE_URL`；不打印、不序列化 key | dotenvy |
| `image` | 读文件 → magic bytes 探测格式（JPEG/PNG/GIF/WebP）→ base64 → `data:` URL | base64 |
| `protocol` | 纯类型：`ContentBlock`（Text/ImageUrl/File）、`ChatRequest`、`ChatResponse`、`Usage`、`FileObject`；序列化契约测试 | serde |
| `client` | `chat()` 发 `/chat/completions`；`upload_file()` 发 `/files`（multipart）；错误信息中 redact key | reqwest |
| `prompts` | 各场景的中文提示词常量（含可注入的图片上下文） | — |
| `reporter` | 把任务结果写 `reports/<ts>/<task>.json|md`，汇总索引 | serde_json |
| `main` | clap 子命令分发：`describe / ocr / compare / create / gen-probe / all` | clap |

### 协议关键契约（来自官方文档）

- 请求：`POST {base}/chat/completions`，`model=deepseek-v4-flash-vision-exp`；
  `messages[].content` 为块数组：`{"type":"text","text":...}`、
  `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."[, "detail":"low|high|original|auto"]}}`、
  `{"type":"file","file_id":"file-api-..."}`。
- 图片仅允许出现在 `user` 消息；`system`/`assistant` 带图片 → 400。
- 上传：`POST {base}/files`，multipart 字段 `file` + `purpose=user_data` → `{"id":"file-api-...", ...}`。
- 图片 token 上限 384/张（自动缩放至 ~800×800）；请求体 ≤48 MiB；单图 base64 ≤32 MiB。

## 场景设计

| 场景 | 输入 | 验证点 |
| ---- | ---- | ------ |
| `describe` | 每张图单独 | 单图理解质量；base64 路径（01/02/03）+ Files API 路径（01）双路对照 |
| `ocr` | 01.jpg | 漫画文字提取准确性 |
| `compare` | 01+02+03 一次请求 | 多图同请求、剧情连贯性理解 |
| `create` | 01.jpg | 创作能力：剧情续写 + 分镜脚本 + 画风分析 |
| `gen-probe` | 01.jpg（必要时无图） | 多模态生成探测：SVG / ASCII 画 / 图片 base64 / 图片 URL 四连问，判断是否支持图像输出 |
| `all` | 全部 | 顺序执行以上全部场景 |

## 测试策略（TDD）

1. **协议测试**（tests/protocol.rs + 单元）：请求体 JSON 结构快照、响应解析（含 usage、file object）。
2. **图片测试**：magic-byte 探测（构造各格式字节样本）、base64 data URL 正确性。
3. **配置测试**：env 注入读取、缺失 key 报错信息不含 key、.env 加载。
4. **客户端测试**（httpmock）：mock `/chat/completions` 与 `/files`，断言请求头/体结构与响应解析。
5. **真实 API 冒烟**（`#[ignore]` + CLI）：手动触发，跑通全部场景，产物落 `reports/`。

## 安全设计

- API key 仅存于 `.env`（chmod 600），项目 `.gitignore` 忽略 `.env`、`*.key`、`secrets/`；
  quest-mvp-lab 共享层已有证书/密钥护栏，三层 gitignore 叠加。
- `config` 与 `client` 任何错误信息、日志、报告均不输出 key（redact 处理）。
- 报告产物（`reports/`）默认 gitignored，仅保留脱敏后的模型输出与元数据。

## 非目标

- 不做流式输出、不做多轮对话持久化（场景均为单轮请求）。
- 不做图片下载/URL 抓取路径（本地文件是唯一输入源）。
- 不实现 Anthropic / Responses API 兼容端点（OpenAI 兼容路径已覆盖核心验证）。
