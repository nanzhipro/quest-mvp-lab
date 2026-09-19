"""GraphRAG 混合检索 MVP —— 最小可验证实现。

模块划分（数据流自左向右）：

    corpus  ->  chunk  ->  ┌ lexical (BM25)         ┐
                           ├ dense   (bge 向量)     ├─ RRF 融合 -> answer (DeepSeek)
                           └ graph   (LLM 抽三元组) ┘
                                    └ communities (标签传播 + LLM 摘要)

设计原则：
1. 每个检索通道都是独立可替换的实现，融合层不认识具体通道 —— 新增通道不改融合层。
2. 所有 LLM 调用走同一个 `llm.LLM` 协议，离线测试注入 `ScriptedLLM` 即可全绿。
3. 规则（代码）与数据（corpus/artifacts/runs）严格分离：产物目录可整体删除重建。
"""

__version__ = "0.1.0"
