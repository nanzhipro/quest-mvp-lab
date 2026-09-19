# SPEC — graphrag-hybrid-mvp

本文件定义 MVP 的**范围、契约与验收标准**。README 讲背景与用法，本文件讲「做到什么算完成」。

## 1. 要验证的假设

朴素 RAG（切块 + 向量）在企业知识库的**跨文档多跳提问**上会失效：
答案不在任何单篇文档里，需要把分散在几篇文档里的条款串起来。

本 MVP 要验证：**把文档先变成知识图谱再做混合检索，能否在多跳问题上显著提升召回**，
以及这套做法在最小工程量下是否可复现、可评测、可进回归。

## 2. 范围

**做**

1. 语料层：Markdown + frontmatter → 按小节切分为 chunk（带稳定可引用 id）。
2. 四条检索通道：`lexical`(BM25) / `dense`(中文向量) / `graph`(LLM 抽取的实体关系图) / `community`(图上话题簇摘要)。
3. 融合层：RRF 加权融合，保留每个命中的通道来源与通道内排名。
4. 回答层：DeepSeek 生成带 `[n]` 引用的答案，资料不足必须拒答。
5. 评测层：带金标准的问题集 + 通道消融指标（文档级 recall@k / all-gold@k / MRR）。
6. 索引幂等：抽取、向量、社区摘要三类缓存按内容哈希复用；改一篇文档只补算增量。

**不做（明确的非目标）**

- 不做 Web UI / 服务化部署（无 HTTP server、无并发查询压测）。
- 不做真实图谱数据库（Neo4j/Neptune）；图就是内存 + JSON，量级目标是百级实体。
- 不做 PDF/OCR 抽取（语料就是 Markdown；格式转换不属于本 MVP 要验证的假设）。
- 不做多用户、权限、增量变更监听（那是产品化议题）。
- 不追求 SOTA 抽取质量；抽取提示词是够用即可，重点是**链路可跑通 + 可评测**。

## 3. 契约

### 3.1 CLI

| 命令 | 语义 | 必须产出 |
| --- | --- | --- |
| `graphrag-mvp build` | 构建四通道索引 | `artifacts/{chunks,embeddings}.jsonl`、`graph.json`、`communities.json` + 构建报告 JSON |
| `graphrag-mvp ask "Q"` | 检索 + 生成答案 | 答案文本 + 引用文档 id 列表 + token 用量 |
| `graphrag-mvp search "Q" --channel C` | 单通道命中（调参用） | 命中列表（chunk_id / 分数 / 排名） |
| `graphrag-mvp eval --out DIR` | 通道消融 | `DIR/eval.json`、`DIR/eval.md`；加 `--answers` 时另有 `DIR/answers.jsonl` |
| `graphrag-mvp stats` | 索引与图谱概览 | JSON（chunk/文档/实体/关系/社区/高频实体） |

退出码：成功 `0`；参数非法（未知通道/未知子命令）非零并给出可操作提示。

### 3.2 数据契约

- `Chunk.chunk_id` = `<doc_id>#<序号>`：**引用锚点的唯一形式**，答案里的 `[n]` 最终要落回它。
- `FusedHit.channels` = `{通道名: 该通道内排名}`：任何命中都必须能回答「谁把它捞出来的」。
- `Relation.chunk_ids`：每条边都必须能退回原文，**不允许出现无出处的节点或边**。
- 评测金标准 `gold_docs` 为**文档级**：多跳题只有覆盖全部 gold 文档才算答得出来（chunk 级不计分，避免切分粒度噪声）。

### 3.3 配置契约

全部经环境变量（或 `.env`）注入，代码里不写死：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、
`GRAPHRAG_LLM_MODEL`、`GRAPHRAG_EMBED_MODEL`、`GRAPHRAG_TOP_K`、`GRAPHRAG_GRAPH_HOPS`、
`GRAPHRAG_WEIGHTS`、`GRAPHRAG_WORKERS`、`HF_ENDPOINT`。密钥只进内存，不进产物与日志。

## 4. 验收标准

下列每一条都必须有**可复现的命令 + 真实输出**作为证据。

| # | 验收项 | 判定方式 |
| --- | --- | --- |
| A1 | 离线单测全绿，且不联网、不下载模型 | `uv run pytest` 通过；测试内使用 `ScriptedLLM` + `HashingEmbedder` |
| A2 | 全链路可在真实模型下跑通 | `build` 产出四类产物且实体/关系数 > 0；`ask` 返回带引用的答案 |
| A3 | 索引幂等 | 同语料二次 `build`：新算向量 = 0，模型调用 = 0 |
| A4 | 增量正确 | 新增一篇文档后，只对新增 chunk 做向量与抽取 |
| A5 | 混合检索相对单通道有可验证的收益，且**负结果同样要如实记录** | `eval` 输出中 hybrid 的文档级覆盖 ≥ 任一单通道；证据关键词召回 ≥ 词法通道；逐题对照表里必须列出 hybrid 独有命中的题与 hybrid 丢失的题（`runs/<ts>/eval.json` 的 `per_question`） |
| A6 | 图谱通道能覆盖多跳链 | 多跳问题的 `graph` 单通道能召回全部 gold 文档 |
| A7 | 答案可溯源 | 答案级评测的引用召回 > 0，且引用只取自本次上下文内的 chunk |
| A8 | 拒答能力 | 问语料外的问题时回答「资料不足」，不编造条款 |
| A9 | 产物隔离 | `--out runs/<时间戳>` 不覆盖历史运行；`artifacts/` 可整体删除重建 |

## 5. 复现步骤（评审用）

```bash
cd graphrag-hybrid-mvp
cp .env.example .env            # 填 DEEPSEEK_API_KEY
uv sync --extra dev
uv run pytest                   # A1
uv run graphrag-mvp --artifacts artifacts build        # A2/A3/A4（再跑一次验证 A3）
uv run graphrag-mvp ask "Nebula 高风险变更最后拍板的人是谁？" --evidence
uv run graphrag-mvp eval --answers --out runs/$(date +%Y%m%d-%H%M%S)   # A5/A6/A7
uv run graphrag-mvp ask "公司食堂几点开饭？" --evidence                # A8
```

## 6. 验收结果（本机 macOS 26.5.2 / Python 3.13.14，2026-09-19）

| # | 判定 | 证据 |
| --- | --- | --- |
| A1 | ✅ | `uv run pytest` → 114 passed（全程离线，无网络下载） |
| A2 | ✅ | `build`：22 篇 → 108 chunk；292 实体 / 420 关系 / 14 社区（14 条摘要）；`ask` 返回带 `[n]` 引用的答案 |
| A3 | ✅ | 二次 `build`：`embedded_chunks=0`、`llm_stats.calls=0`、`duration_s=0.05` |
| A4 | ✅ | 新增一篇文档后仅新 chunk 进入向量与抽取（`tests/test_pipeline.py::test_adding_a_document_only_embeds_the_new_chunks`） |
| A5 | ✅（含负结果） | hybrid 文档覆盖 = 最强单通道（all-gold 0.750）；关键词召回 0.896 > lexical 0.854；**未**在文档覆盖上超越 BM25，逐题得失已列表（README §验证结果） |
| A6 | ✅ | q12/q15 只有 `graph` 单通道能覆盖全部 gold 文档 |
| A7 | ✅ | 答案级引用召回 0.859（hybrid）/ 0.839（lexical），引用只取自本次上下文 chunk |
| A8 | ✅ | 语料外提问 → 「资料不足」，0 引用 |
| A9 | ✅ | `runs/<时间戳>/` 独立目录；`artifacts/` 删除后可由 `build` 完整重建 |

## 7. 已知取舍与后续

- **实体链接是确定性的**（字面命中 + 令牌重合），不调模型：牺牲少量召回换取评测可复现。
  后续可加一路「LLM 实体链接」并作为独立通道参与消融。
- **社区检测用标签传播**：规模上千节点后应换 Leiden（`graspologic`）并复测摘要质量。
- **向量是 ONNX/CPU 单进程**：语料上万 chunk 时需换批量 GPU 或外部向量库（Milvus/Qdrant）。
- **评测集只有 16 题**：够验证趋势，不足以做统计显著结论；扩充到 100+ 题才谈得上调参。
