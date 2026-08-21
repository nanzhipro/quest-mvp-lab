# SPEC — DeepSeek 视觉多模态模型验证 MVP（可复现规格）

> **用途**：本文件是完整、可执行的规格说明。任何 AI 编码代理（或工程师）拿到本文件，即可从零生成与本 MVP 等价的项目，无需参考源码。
> **依据**：2026-08-21 真实构建 + 真实 API 验证（3 张本地图片，全链路通过）的成功经验，含全部踩坑记录。
> **目标产物**：Rust 二进制 CLI `ds-vision`，验证 `deepseek-v4-flash-vision-exp` 的理解 / 创作 / 还原能力，产出单文件三列对比报告。

---

## 1. 背景与目标

DeepSeek 于 2026-08-21 发布首个视觉多模态模型 `deepseek-v4-flash-vision-exp`（实验性）。官方文档只承诺**图像理解**（图片+文本输入），未提及图像生成。本 MVP 用本地图片实测回答：

1. **理解**：单图描述、文字提取（OCR）、多图连贯性分析。
2. **创作**：基于画面内容的剧情续写、分镜脚本、画风点评。
3. **还原**（本项目核心亮点）：图像 → 理解文本 → **仅凭文本**再次调用视觉模型 → SVG → PNG，验证「理解 → 视觉重构」跨模态创作闭环。
4. **生成探测**：模型是否能输出图片文件/URL/base64 图像？（预期：不能，但能以 SVG/ASCII 代码媒介创作）

**测试素材**：3 张本地图片（电影《欢迎来龙餐馆》海报：2000×2800 JPEG、2000×2800 WebP、800×1142 JPEG），放 `testimages/`。

## 2. 功能需求

CLI 子命令（clap derive）：

| 命令 | 参数 | 行为 |
| ---- | ---- | ---- |
| `describe <images...> [--via base64\|file-api]` | 图片路径 | 每张图单独描述（base64 内联；`--via file-api` 走 Files API 上传→file_id 引用） |
| `ocr <image>` | 单图 | 提取图片全部文字（对白/标题/招牌，按位置标注） |
| `compare <images...>` | 多图 | 一次请求多图，跨图剧情连贯性分析 |
| `create <image>` | 单图 | 剧情续写 + 分镜脚本 + 画风点评 |
| `gen-probe [image]` | 可选图 | 多模态生成探测：SVG / ASCII / 图片 URL / base64 四连问 + 能力边界自述 |
| `recreate <images...>` | 多图 | 每张图：带原图 → 模型生成 SVG（自动重试至 XML 合法）→ 保存 assets/ |
| `report <images...>` | 多图 | **主交付**：单文件三列对比报告（见 §6） |
| `all` | — | 顺序执行上述全部（11 项任务） |

**报告**：每次运行写入 `reports/<YYYYMMDD-HHMMSS>/`（`report.md` + `report.json`；还原图与缩略图在 `assets/`）。**`reports/` 只保留一份最新报告**（新运行删除旧目录）、**可提交入库**（不 gitignore）。

## 3. 技术选型

| 项 | 选择 | 理由 |
| -- | ---- | ---- |
| 语言 | Rust 2021 edition | 类型安全、模块化、TDD 友好 |
| HTTP | `reqwest` (blocking + json + multipart) | multipart 支持 Files API 上传 |
| JSON | `serde` / `serde_json` | 协议强类型化 |
| base64 | `base64` crate | data URL 编码 |
| CLI | `clap` (derive) | 子命令 |
| 配置 | `dotenvy` + `std::env` | `.env` 加载 |
| 测试 | 单元测试 + `httpmock`（mock server） | 零网络依赖 |
| 图片处理 | 自实现 magic-byte 探测（JPEG/PNG/GIF/WebP）+ base64 | 无重依赖 |
| 系统工具（macOS） | `sips`（缩略图/PNG 校验）、`qlmanage`（SVG→PNG）、`xmllint`（SVG XML 校验） | 系统自带，零额外依赖 |

## 4. 模块划分（高内聚、低耦合）

```
src/
├── main.rs       # clap CLI + 任务编排（describe/ocr/compare/create/gen-probe/recreate/report/all）
├── lib.rs        # 根 re-export
├── config.rs     # Config：env/.env 读取 DEEPSEEK_API_KEY / DS_MODEL / DS_BASE_URL；Debug/Display/错误路径全部脱敏
├── image.rs      # detect_format（magic bytes）/ to_data_url / file_to_data_url（≤32MiB 检查）
├── protocol.rs   # ContentBlock(text/image_url/file) / ChatRequest / ChatResponse / Usage / FileObject / ApiError / Thinking
├── client.rs     # DeepSeekClient：chat() / chat_with_images() / upload_file()；错误信息 redact key
├── prompts.rs    # 场景提示词常量（DESCRIBE/OCR/COMPARE/CREATE/RECREATE/RECREATE_RETRY + 文本驱动版）
└── reporter.rs   # TaskResult / Report（通用）+ CompareRow / CompareReport（三列对比）+ ReportWriter
tests/            # protocol_tests / image_tests / config_tests / client_tests（httpmock）
```

**公共 API 要点**（`lib.rs` re-export）：`Config`、`DeepSeekClient`、`ImageInput::{DataUrl, FileId}`、`ContentBlock`、`ChatRequest`、`ChatResponse`、`Usage`、`FileObject`、`TaskResult`、`Report`、`CompareReport`、`CompareRow`、`ReportWriter`、`detect_format`、`to_data_url`、`file_to_data_url`。

## 5. API 契约（实测确认，DeepSeek 官方文档 2026-08-21）

### 5.1 端点

| 端点 | 方法 | 说明 |
| ---- | ---- | ---- |
| `{base}/chat/completions` | POST | OpenAI 兼容对话补全 |
| `{base}/files` | POST | 上传图片（multipart：`file` 字段 + `purpose=user_data`）→ 返回 `file-id` |

base_url 默认 `https://api.deepseek.com`，模型名 `deepseek-v4-flash-vision-exp`。

### 5.2 请求体（chat）

```json
{
  "model": "deepseek-v4-flash-vision-exp",
  "max_tokens": 12000,
  "thinking": {"type": "disabled"},
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "请描述这张图片"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<BASE64>", "detail": "low|high|original|auto"}},
      {"type": "file", "file_id": "file-api-..."}
    ]
  }]
}
```

### 5.3 响应体

```json
{
  "id": "chatcmpl-...",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "...", "reasoning_content": "..."}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 483, "completion_tokens": 1200, "total_tokens": 1683}
}
```

上传响应：`{"id": "file-api-...", "object": "file", "bytes": N, "created_at": ts, "filename": "...", "purpose": "user_data"}`。

### 5.4 硬限制（必须遵守）

- 图片仅允许在 **user** 消息；system/assistant 带图 → 400。
- 图片格式 JPEG/PNG/GIF/WebP（按内容识别，非文件名）。
- 单图 base64 ≤ 32 MiB；Files API ≤ 64 MiB；请求体 ≤ 48 MiB；单请求 ≤ 600 图。
- 图片自动缩放至 ~800×800 总像素，**每图 ≤384 token**（实测 prompt≈483 含文本）。
- 其他模型（deepseek-v4-flash/pro）收图 → 400。

### 5.5 关键经验（踩坑，必须实现）

1. **思考模式会吃光 max_tokens**：reasoning 开启时可能耗尽整个预算，`content` 返回**空**。→ 短回答/代码生成类任务传 `"thinking": {"type": "disabled"}`。
2. **输出预算**：长创作（续写+分镜+点评）需 ≥8000 token；SVG 还原需 12000 token，否则截断。
3. **模型输出带垃圾前缀**：SVG 围栏后常出现 `g\n<svg...>`（实测高发）。→ 提取后必须裁剪到第一个 `<svg` 起点、最后一个 `</svg>` 结束（见 §7.2）。
4. **"完整闭合"≠合法**：`</svg>` 结尾不代表 XML 合法。→ 必须 `xmllint --noout` 校验 + PNG 渲染验证，不过即重试。

## 6. 三列对比报告（主交付物规格）

`report` 命令输出单文件 `report.md`，每张图一节、三列并排：

| 列 | 内容 | 生成方式 |
| -- | ---- | -------- |
| 左 | 原图缩略图 PNG | `sips -s format png -Z 480` 缩放，存 `assets/original_<stem>.png` |
| 中 | 模型理解全文 | 第 1 轮：原图 + DESCRIBE 提示词 |
| 右 | 还原图 PNG | 第 2 轮：**仅凭中列文本**（不带图）→ SVG → `qlmanage` 渲染 → `assets/recreated_from_text_<stem>.svg.png` |

每节下方：`SVG 源码路径` + `理解: X.Xs（N token）｜还原: X.Xs（N token，M 次尝试）`。

Markdown 表格单元格内多段文本用 `<br>` 分隔（GitHub 兼容）。渲染模板：

```
| 原图 | 模型理解输出 | 还原图（理解文本 → SVG → PNG） |
| ---- | ------------ | ------------------------------ |
| ![](assets/original_01.png) | 文本…<br><br>段落… | ![](assets/recreated_from_text_01.svg.png) |
```

## 7. 关键实现细节

### 7.1 双传图路径（base64 + Files API）

- base64：读文件 → magic-byte 探测格式 → `data:<mime>;base64,<payload>`。
- Files API：`POST /files`（multipart `file` + `purpose=user_data`）→ 拿 `file_id` → chat 请求用 `{"type":"file","file_id":...}` 块。01.jpg 双路径对照，输出质量一致。

### 7.2 SVG 提取与校验（还原/探测必用）

```text
extract_svg(raw):
  1) 找 "```svg" 围栏 → 取围栏内文本；无围栏则用全文
  2) 裁剪：从第一个 "<svg" 开始，到最后一个 "</svg>" 结束（trim）
  3) 若无 <svg → 原样 trim
```

校验链（不通过即重试，最多 5 次，重试提示词要求"简化版、必须 </svg> 闭合、无解释文字"）：
`非空` → `以 </svg> 结尾` → 保存后 `xmllint --noout` 通过 → `qlmanage` 渲染 → `sips -g pixelWidth` 可读且文件 ≥256B。

### 7.3 重试提示词模式

首次：完整还原（viewBox 0 0 800 1200，元素 ≤80）。
重试：**简化但完整闭合**（核心 3-5 个主体，元素 ≤40，必须 `</svg>` 结尾，无解释文字）。
文本驱动版同样结构，但把画面描述嵌入 `"""..."""` 传入，**不带原图**。

### 7.4 安全（API Key）

- 仅存 `.env`（chmod 600）；`.env.example` 为可提交模板（占位 key）。
- `.gitignore`：`.env`、`.env.*`、`!.env.example`、`*.key`、`*.pem`、`secrets/`；**不忽略** `reports/`、`Cargo.lock`。
- `Config` 的 Debug/Display 输出 `[REDACTED]`；错误路径 `redact()` 防御性替换；测试断言错误信息永不包含 key。

## 8. 测试规格（TDD，≥33 个测试全绿 + clippy 零警告）

| 测试文件 | 覆盖 | 要点 |
| -------- | ---- | ---- |
| `tests/protocol_tests.rs` | 协议序列化/反序列化 | 三种 content block 的 JSON 形状；无 detail 时省略字段；usage/error/file object 解析 |
| `tests/image_tests.rs` | 格式探测 + data URL | JPEG/PNG/GIF/WebP magic bytes；未知格式报错；base64 往返 |
| `tests/config_tests.rs` | 配置与脱敏 | **全局 Mutex 串行化**（env 变量测试并行会竞态）；缺失 key 报错；Debug/Display/redact 不含 key |
| `tests/client_tests.rs` | mock 客户端 | **httpmock 0.7 无 `mock.matches()`**——请求体断言用 `when.json_body(...)` 声明式匹配；`json_body` 必须传 `serde_json::Value`（传 `&str` 会双重编码）；multipart 用 `body_contains`；API 错误信息不含 key |
| `reporter` 内嵌测试 | 报告渲染 | 通用报告章节完整性；三列对比表格结构（含 `<br>` 换行与耗时/token 统计） |

**httpmock 0.7 陷阱**：`when.json_body(r#"..."#)` 传字符串会把请求体比成 JSON 字符串值——必须 `when.json_body(serde_json::json!({...}))`。

## 9. 构建 / 运行 / 验证

```bash
cargo build --release          # → target/release/ds-vision
cargo test                     # ≥33 tests green
cargo clippy --all-targets     # 0 warnings

cp .env.example .env           # 填入 DEEPSEEK_API_KEY
./target/release/ds-vision report testimages/*.jpg testimages/*.webp   # 主交付
./target/release/ds-vision all                                        # 全场景 11 项
```

## 10. 验收标准（Definition of Done）

- [ ] `cargo test` 全绿（≥33 测试），`cargo clippy --all-targets` 零警告
- [ ] 真实 API 全链路通过：3 张图 × 理解/OCR/对比/创作/还原；01.jpg base64 + Files API 双路径
- [ ] `report` 命令产出单文件三列对比报告，3 张图各一行
- [ ] 3 张还原 SVG 全部 `xmllint` 合法、PNG 全部可打开（非退化渲染）
- [ ] 报告含：理解耗时/token、还原耗时/token/尝试次数、SVG 源码路径
- [ ] API Key 零泄漏（代码/日志/报告/git 均无 key；`.env` 被忽略、`.env.example` 可提交）
- [ ] `reports/` 仅一份最新报告，可提交
- [ ] README（中文）含：能力范围与限制、性能实测（图片参数+模型性能对比表）、三列对比报告说明、构建/运行/验证命令

## 11. 已验证结论（供 SPEC 使用者直接引用）

- `deepseek-v4-flash-vision-exp` 是**纯文本输出的图像理解模型**：无图片文件/URL/base64 图像生成（模型诚实拒绝并自述边界），但可生成**完整可渲染的 SVG** 与 ASCII 画（代码媒介创作）。
- 理解质量：3/3 海报高保真描述、中英阿混排 OCR 准确、多图可建立跨图叙事链。
- 还原闭环：3/3 张图「图像 → 文本 → 仅凭文本重构 SVG → PNG」成功。
- 性能参考（同 prompt 同预算）：prompt token 三图一致（483，印证自动缩放封顶）；输出 token 与耗时随画面复杂度递增（01 复杂图 21.5s vs 03 简单图 6.2s）；吞吐 ≈60 completion token/s。
