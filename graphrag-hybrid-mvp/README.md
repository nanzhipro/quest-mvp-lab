# graphrag-hybrid-mvp

> 最小可验证的 **GraphRAG 混合检索** MVP：把一个中文文档库同时建成
> **BM25 词法索引 + 中文向量索引 + LLM 抽取的知识图谱**，四路召回用 RRF 融合，
> 再把带编号的证据交给 DeepSeek 生成**带引用、拒答无据**的答案。

<!-- 一句话结论 — 完整数据见「验证结果」 -->
实测（22 篇中文文档 / 108 个 chunk / 16 道评测题，top_k=12）：混合检索在**文档覆盖**上追平最强单通道
（all-gold 0.750），在**证据关键词召回**上优于词法通道（0.896 vs 0.854，多跳 0.833 vs 0.750），
但**没有出现「碾压单通道」的收益** —— 22 篇语料下字符级 BM25 已经很强。真正被证实的机制是：
纯 RRF 会把图谱通道找出的「链条最后一环」挤掉，加**通道保底**后该题被救回（q12 / q16）。

## 背景：为什么需要 GraphRAG

朴素 RAG（切块 + 向量）在**单跳事实题**上够用，但企业知识库的真实提问往往是**跨文档多跳**的：

- 问：「Nebula 平台的高风险变更最终由谁签字？」
- 文档 A：Nebula 高风险变更走 **CR-2** 流程
- 文档 B：**CR-2** 由 **安全审批人** 审批
- 文档 C：安全审批人由安全合规部负责人担任，当前为 **张岚**

答案是「张岚」，但它**不在任何一篇文档里**。向量检索擅长语义相似、BM25 擅长术语命中，
两者都只能捞到「和问题长得像」的片段 —— 而这条链的最后一环与问题几乎没有字面重叠。

GraphRAG 的做法是把文档先变成**实体-关系图**，检索时沿图走：命中 `Nebula` → `CR-2` →
`安全审批人` → `张岚`，再把链条上每个节点的**原文出处**一起取回来。
混合检索 = 图谱这条路 + 词法/向量两条老路，用 RRF 融合。

## 架构

```
corpus/*.md
   │  按小节切分（heading-aware，chunk_id = doc-05#2，可直接做引用锚点）
   ▼
┌──────────────┬───────────────┬────────────────────┬────────────────────┐
│ lexical      │ dense         │ graph              │ community          │
│ 自研 BM25    │ bge-small-zh  │ LLM 抽实体/关系    │ 标签传播分簇       │
│ 中英混排分词 │ ONNX/CPU 向量 │ + 别名归并 + BFS   │ + LLM 簇摘要       │
└──────┬───────┴──────┬────────┴─────────┬──────────┴─────────┬──────────┘
       │              │                  │                    │
       └──────────────┴──────────┬───────┴────────────────────┘
                                 ▼
                     RRF 加权融合（k=60，权重可配）
                                 ▼
              Top-K 证据块 + 话题簇摘要 → DeepSeek → 带 [n] 引用的答案
```

模块对应关系（`src/graphrag_mvp/`）：

| 文件 | 职责 |
| --- | --- |
| `corpus.py` | frontmatter 解析 + 小节切分（过长按段落二次切、过短向前合并） |
| `lexical.py` | 中英混排分词 + Okapi BM25（自研，无 `jieba`/`rank_bm25` 依赖） |
| `embedding.py` | `OnnxEmbedder`（fastembed/bge-small-zh）与 `HashingEmbedder`（离线降级）+ 向量磁盘缓存 |
| `graph.py` | LLM 抽取 → 解析容错 → 实体规范化/别名合并 → 关系归并 → BFS 展开/反向出处索引 |
| `communities.py` | 确定性标签传播分簇（平票取字典序，保证可复现） |
| `summarize.py` | 社区摘要（LLM，按成员哈希缓存） |
| `fusion.py` | RRF 加权融合 + 来源审计（每个命中记录了它被哪几个通道、排第几捞出来） |
| `retriever.py` | 四通道调度 + 通道开关/权重 + 实体链接（字面命中，确定性、不调模型） |
| `answer.py` | 证据编号、反幻觉 system prompt、`[n]` → 文档 id 的引用解析 |
| `pipeline.py` | 索引构建/装载，幂等增量（改一篇文档只补算新 chunk） |
| `evaluate.py` | 文档级 recall@k / all-gold@k / MRR 的通道消融 |
| `cli.py` | `build` / `ask` / `search` / `eval` / `stats` |

## 关键设计决策

1. **图谱抽在 chunk 上，链接靠实体名**。抽取粒度小 → 提示词短、可并行、可缓存；
   实体链接（问题 → 图上的种子节点）用**字面命中 + 令牌重合**，不调模型，
   这样检索评测**可复现、可离线、可进 CI**——模型只出现在建索引的抽取/摘要阶段。

2. **实体别名必须归并**。`Nebula` / `Nebula 平台` / `Ｎｅｂｕｌａ　平台` 若不折叠成同一节点，
   多跳链会在第一跳就断掉。归并规则：NFKC 折叠 + 去空白 + 括号统一 + 别名投票选规范名。

3. **RRF 而不是加权分数求和**。BM25 分数无界、余弦在 [-1,1]、图谱分数是手写权重，
   三者不可比；RRF 只用排名，免归一化、免调参，权重只用来表达「更信哪条通道」。

4. **社区用标签传播而非 Leiden**。几百节点的企业库上效果相当，但实现无依赖、无随机性
   （节点按度数排序、平票取字典序最小标签）——可复现比省几行代码重要。

5. **每条产物都可追溯**。`FusedHit.channels` 记录每个命中的通道来源与通道内排名，
   关系边记录它在哪些 chunk 被说过 —— 答案里每个 `[n]` 都能退回原文。

6. **引用是硬约束**。system prompt 要求「只依据资料、资料不足就说资料不足、结论必须带编号」，
   评测里同时看**关键词覆盖**（答对内容）与**引用召回**（引对依据）两个指标。

## 目录结构

```
graphrag-hybrid-mvp/
├── corpus/                 # 22 篇虚构企业内部制度/流程文档（中文）
├── eval/
│   ├── questions.jsonl     # 16 道带金标准的问题（8 单跳 + 8 多跳）
│   └── chains.md           # 多跳链条的逐条说明
├── src/graphrag_mvp/       # 主包（见上表）
├── tests/                  # 离线单测（ScriptedLLM + HashingEmbedder，不联网）
├── runs/<时间戳>/          # 评测证据（输出隔离，不覆盖历史）
├── artifacts/              # 索引产物（可删除重建）
├── SPEC.md                 # MVP 规范：范围、契约、验收标准
└── pyproject.toml          # uv / ruff / pytest 配置
```

## 快速开始

```bash
cd graphrag-hybrid-mvp
cp .env.example .env          # 填入 DEEPSEEK_API_KEY（.env 已 gitignore）
uv sync --extra dev           # Python 3.11+，装 fastembed(ONNX) + numpy + pytest/ruff
uv run pytest                 # 离线单测全绿（不联网、不下载模型）
```

### 1. 构建索引

```bash
uv run graphrag-mvp --artifacts artifacts build --corpus corpus
#  语料：22 篇文档 → 118 个 chunk
#  向量：118 条（模型 onnx:BAAI/bge-small-zh-v1.5）
#  图谱：xxx 个实体 / xxx 条关系
#  社区：xx 个（已生成摘要 xx 条）
```

首次运行会通过 `HF_ENDPOINT=https://hf-mirror.com` 拉取 bge-small-zh-v1.5（约 95MB）。
模型/网络不可用时加 `--embedder hashing` 走离线降级路径（向量质量下降，但全链路仍可跑通）。

### 2. 提问（带证据）

```bash
uv run graphrag-mvp ask "Nebula 平台的高风险变更最终由谁签字审批？" --evidence
# 通道：lexical、dense、graph、community
# 答案：A 规定高风险变更必须走 CR-2 [1] → CR-2 由安全审批人审批 [2]
#       → 安全审批人当前由张岚担任 [3]。因此最终签字人是张岚 [3]。
# 引用文档：doc-01, doc-02, doc-03
```

对比单通道（把其它通道关掉）：

```bash
uv run graphrag-mvp ask "同一问题" --channels lexical          # 只捞到字面命中的片段
uv run graphrag-mvp ask "同一问题" --channels graph --evidence # 沿图走完整条链
uv run graphrag-mvp ask "同一问题" --weights graph:3,lexical:1 # 调权重
uv run graphrag-mvp search "审批" --channel lexical            # 看单通道原始命中与分数
```

### 3. 通道消融评测

```bash
uv run graphrag-mvp eval --out runs/$(date +%Y%m%d-%H%M%S)
uv run graphrag-mvp eval --answers --out runs/$(date +%Y%m%d-%H%M%S)   # 追加 LLM 答案级评分
```

产物落在 `runs/<时间戳>/`：`eval.json`（含每题召回明细）、`eval.md`、`answers.jsonl`。

### 4. 索引概览

```bash
uv run graphrag-mvp stats
```

## 验证结果

> 复现命令与原始产物见 `runs/<时间戳>/`；下列数字来自本机 macOS 26.5.2 + Python 3.13.14
> 的一次完整运行（语料 22 篇 → 108 chunk；图谱 292 实体 / 420 关系 / 14 社区；
> 索引构建 122 次 LLM 调用、55k prompt + 22k completion tokens、28 秒）。

**复现命令**：

```bash
uv run graphrag-mvp --artifacts artifacts eval --out runs/$(date +%Y%m%d-%H%M%S)
uv run graphrag-mvp --artifacts artifacts eval --answers --channels lexical --out runs/<ts>/answers-lexical
```

### 通道消融（top_k=12，图谱 3 跳，通道保底 1）

| 通道组合 | recall@K | all-gold@K | 关键词召回 | MRR | 单跳 recall | 多跳 recall | 多跳 all-gold | 多跳关键词 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lexical(BM25) | 0.917 | 0.750 | 0.854 | **0.807** | 1.000 | 0.833 | 0.500 | 0.750 |
| dense(向量) | 0.885 | 0.688 | **0.917** | 0.906 | 1.000 | 0.771 | 0.375 | 0.875 |
| graph(图谱) | 0.755 | 0.562 | 0.750 | 0.617 | 0.875 | 0.635 | 0.250 | 0.750 |
| lexical+dense | 0.917 | 0.750 | 0.896 | 0.969 | 1.000 | 0.833 | 0.500 | 0.833 |
| **hybrid(四通道)** | **0.917** | **0.750** | 0.896 | 0.755 | 1.000 | 0.833 | **0.500** | 0.833 |

指标口径（全部文档级，`evaluate.py`）：

- `recall@K`：检出 gold 文档占 gold 文档总数的比例。
- `all-gold@K`：**是否覆盖全部** gold 文档 —— 多跳问题只有这一项为真才算「真检索到了」。
- **关键词召回**：`answer_keywords` 是否真的出现在上下文里。比文档命中更严格，
  回答「链条最后一环的那条事实到底进没进上下文」。
- `MRR`：首个 gold 文档的排名倒数。

### 读法（这批数字真正说明什么）

1. **没有出现预期的碾压**：hybrid 在文档覆盖上追平 BM25（0.750 / 0.750），
   在证据关键词召回上比 BM25 高 4~8 个百分点，但 MRR 反而更低（0.755 vs 0.807）。
   原因是语料只有 22 篇、章节之间共用大量制度用语，字符双字 BM25 的召回已经接近饱和；
   混合检索的收益被「强单通道 + 宽松的文档级指标」吃掉了。
2. **图谱通道单独用最弱**（all-gold 0.562），但它能覆盖别人覆盖不了的多跳链。
   逐题看 8 道多跳题的**全链覆盖**：

   | 题 | gold | lexical | dense | graph | hybrid(保底=0) | hybrid(保底=1) |
   | --- | --- | --- | --- | --- | --- | --- |
   | q09 | doc-01/08/09 | ✅ | ✅ | ✗ | ✅ | ✅ |
   | q12 | doc-12/13/16 | ✗ | ✗ | ✅ | ✗ | **✅** |
   | q15 | doc-17/01/13 | ✗ | ✗ | ✅ | ✗ | ✗ |
   | q16 | doc-21/14 | ✅ | ✗ | ✗ | ✅ | ✅ |
   | q10/q11/q13/q14 | — | 部分 | 部分 | ✗ | — | — |

   q12（`IR-3 事故谁拍板`）只有图谱通道能连出 `IR-3 → 事件指挥 → 安全合规部值班人` 这条链；
   纯 RRF 融合时它被「多通道都提到」的热门片段挤出 top-12，**加了通道保底才重新进入上下文**。
   这就是本 MVP 验证到的核心机制：**融合策略决定图谱通道的收益能不能落地**。
3. **代价是明确可见的**：保底占用尾部席位，MRR 与 q11 会因此受损（保底=1 时 q11 丢失）。
   `--channel-floor 0` 可以关掉它，两套数字在 `runs/` 里都有留档。

### 答案级评分（DeepSeek，`--answers`，16 题）

| 配置 | 关键词覆盖 | 引用召回 |
| --- | --- | --- |
| lexical(仅 BM25) | 0.771 | 0.839 |
| **hybrid(四通道)** | **0.854** | **0.859** |

答案级指标上的差距比检索级更明显：混合检索多带入的「链条末端证据」直接体现在答案里，
同一批 16 题里关键词覆盖从 0.771 提到 0.854。原始答案逐条留档在
`runs/<ts>/answers-hybrid/answers.jsonl` 与 `answers-lexical/answers.jsonl`。

### 端到端示例（真实运行输出）

```bash
uv run graphrag-mvp ask "核心服务挂了 40 分钟这种 IR-3 事故，现场谁拍板决定回滚？"
```

```
答案：核心服务不可用持续 30 分钟及以上属 IR-3 [4]，须指定事件指挥，事件指挥对处置拥有最高现场决策权，
      包括决定回滚 [2]。事件指挥由 SRE 值班经理担任，本季度为李默 [2][12]。故由李默拍板回滚；
      其不在线或超 10 分钟无法联系时，由基础架构部值班工程师临时代理，代理决策同样有效 [2]。
引用文档：doc-12, doc-13, doc-16      Token：prompt=2079 completion=98
```

三篇文档各出一环（事件分级 → 事件指挥归属 → 本季值班人），模型把三跳串成了「李默」。

### 反幻觉（语料外提问）

```bash
uv run graphrag-mvp ask "公司食堂几点开饭，有什么菜？"
# 答案：资料不足。所有资料均未涉及公司食堂开饭时间或菜品内容。
# 引用文档：（无）    Token：prompt=1942 completion=17
```

同一测法反证了「资料没写就不答」：0 引用、0 编造，仅回答「资料不足」。

### 索引幂等与增量（真实输出）

```bash
# 第二次 build（同语料）
#   向量：108 条（本次新算 0 条，模型 onnx:BAAI/bge-small-zh-v1.5）
#   图谱：292 个实体 / 420 条关系（孤立节点 39）
#   duration_s: 0.05   llm_stats: {"calls": 0, "cache_hits": 0}
```

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `缺少 DEEPSEEK_API_KEY` | 没有 `.env`；`cp .env.example .env` 后填 key |
| 模型下载卡住 | HuggingFace 直连不通，`.env` 里保留 `HF_ENDPOINT=https://hf-mirror.com`；或 `--embedder hashing` |
| 图谱实体数远小于预期 | 抽取解析失败会降级为空抽取，检查 `artifacts/extractions.jsonl` 里是否有空记录 |
| 多跳题仍答不出 | 先把 `--channels graph` 单独跑一遍：若图谱通道也拿不到，多半是别名没归并（看 `artifacts/graph.json`） |
| 重复运行很慢 | 首次之后抽取/向量/摘要全部命中缓存；`stats` 与 `build` 的输出会显示本次新算了多少 |

## 结论

- **混合检索的收益不是自动的**：通道多不等于召回高。在 22 篇小语料上，字符双字 BM25 已经接近饱和，
  四通道融合只在「证据关键词召回」上稳定优于词法通道，文档覆盖只是追平。
- **融合策略才是图谱通道收益的开关**：图谱能找到链条最后一环，但纯 RRF 会把它挤掉；
  通道保底让 q12 这类问题从「答不出」变成「答得出」，代价是 MRR 与个别题目。
- **图谱的质量取决于实体归并，不是抽取模型的强弱**：`Nebula` / `Nebula 平台` / `Ｎｅｂｕｌａ　平台`
  不折叠成同一节点，多跳链第一跳就断（`graph.py` 的别名投票逻辑专门处理这一点）。
- **可复现是前提**：模型只出现在建索引的抽取/摘要阶段，检索路径全确定性，
  因此 `eval` 可以当回归测试跑，也可以离线复现（`ScriptedLLM` + `HashingEmbedder`）。
- **下一步该在哪花力气**：语料扩到数百篇、指标换成 chunk 级 + 答案级、图谱通道引入
  （a）跨编码器/LLM 重排 与（b）按路径强度加权的实体链接，再复测是否仍有收益。
  当前结果不足以宣称「GraphRAG 一定更好」，只足以说明「用什么融合方式」比「加不加图谱」更关键。
