"""共享测试夹具。

原则：所有测试都在**离线**下运行 ——
- LLM 一律用 `ScriptedLLM`（确定性的抽取桩，让图谱通道可断言）；
- 向量一律用 `HashingEmbedder`（零依赖、确定性），不下载模型；
- 语料写在 tmp 目录里，测试之间不共享状态。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphrag_mvp.embedding import EmbeddingStore, HashingEmbedder
from graphrag_mvp.graph import KnowledgeGraph, extract_chunks
from graphrag_mvp.lexical import BM25Index
from graphrag_mvp.llm import ScriptedLLM
from graphrag_mvp.pipeline import build_index, load_retriever_index

# 三篇文档构成一条完整的多跳链：Nebula --CR-2--> 安全审批人 --担任--> 张岚
DOCS = {
    "doc-01-nebula-release.md": """---
id: doc-01
title: Nebula 云平台发布窗口
owner: 基础架构部
updated: 2026-03-14
---

## 变更分级

Nebula 平台的变更分为常规变更与高风险变更。高风险变更必须走 CR-2 流程，
发布窗口固定为每周三 21:00 至 23:00，回滚必须在 30 分钟内完成。

## 值班要求

发布期间 SRE 值班经理必须在场，Kestrel 工单需提前 4 小时登记。
""",
    "doc-02-cr2-approval.md": """---
id: doc-02
title: CR-2 高风险变更审批规则
owner: 安全合规部
updated: 2026-02-20
---

## 审批人

CR-2 由安全审批人审批。CR-1 可由系统负责人自行审批。

## 时限

CR-2 的审批时限为 4 小时，超时自动升级到安全合规部负责人。
""",
    "doc-03-approver-roster.md": """---
id: doc-03
title: 安全审批人名单与职责
owner: 安全合规部
updated: 2026-01-05
---

## 当前担任者

安全审批人由安全合规部负责人担任，当前为张岚。安全审批人负责 CR-2 的最终签字。

## 交接

名单变更需在 Kestrel 中登记，生效时间为登记后次日 0 点。
""",
}

MULTI_HOP_QUESTION = {
    "id": "q-multi",
    "question": "Nebula 高风险变更最后拍板的人是谁？",
    "kind": "multi_hop",
    "gold_docs": ["doc-01", "doc-02", "doc-03"],
    "answer_keywords": ["张岚", "安全合规部"],
    "hops": 3,
}
SINGLE_HOP_QUESTION = {
    "id": "q-single",
    "question": "Nebula 平台的发布窗口是每周几几点？",
    "kind": "single_hop",
    "gold_docs": ["doc-01"],
    "answer_keywords": ["周三", "21:00"],
    "hops": 1,
}


def extraction_for(chunk_text: str) -> str:
    """按片段内容返回抽取结果，模拟真实 LLM 在三篇文档上抽出的链式图谱。

    注意：只按**正文**分派，不要用整个提示词（system prompt 里举了 Nebula 作例子）。
    """
    if "张岚" in chunk_text:
        payload = {
            "entities": [
                {"name": "安全审批人", "type": "角色", "aliases": ["安全审批"]},
                {"name": "张岚", "type": "人物", "aliases": []},
                {"name": "安全合规部", "type": "部门", "aliases": []},
            ],
            "relations": [
                {"source": "安全审批人", "target": "张岚", "predicate": "由...担任"},
                {"source": "张岚", "target": "安全合规部", "predicate": "所属"},
            ],
        }
    elif "由安全审批人审批" in chunk_text:
        payload = {
            "entities": [
                {"name": "CR-2", "type": "流程", "aliases": ["高风险变更"]},
                {"name": "安全审批人", "type": "角色", "aliases": []},
            ],
            "relations": [{"source": "CR-2", "predicate": "由...审批", "target": "安全审批人"}],
        }
    elif "Nebula" in chunk_text:
        payload = {
            "entities": [
                {"name": "Nebula", "type": "系统", "aliases": ["Nebula 平台"]},
                {"name": "CR-2", "type": "流程", "aliases": []},
            ],
            "relations": [{"source": "Nebula", "predicate": "适用", "target": "CR-2"}],
        }
    else:
        payload = {"entities": [], "relations": []}
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir()
    for name, text in DOCS.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


@pytest.fixture
def eval_dir(tmp_path: Path, corpus_dir: Path) -> Path:
    directory = tmp_path / "eval"
    directory.mkdir()
    lines = [json.dumps(payload, ensure_ascii=False) for payload in (SINGLE_HOP_QUESTION, MULTI_HOP_QUESTION)]
    (directory / "questions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (directory / "chains.md").write_text("# 链条\n\nNebula → CR-2 → 安全审批人 → 张岚\n", encoding="utf-8")
    return directory


@pytest.fixture
def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM(rules=[("", "")])


@pytest.fixture
def extraction_llm() -> ScriptedLLM:
    """抽取桩：按片段内容分派（规则顺序即优先级）。"""
    from graphrag_mvp.llm import LLMResponse

    class ContentScriptedLLM(ScriptedLLM):
        def complete(self, messages, **kwargs):  # type: ignore[override]
            system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
            user = "\n".join(m.get("content", "") for m in messages if m.get("role") != "system")
            self.calls.append({"prompt": user, **kwargs})
            if "话题簇" in system:  # 社区摘要调用
                return LLMResponse(
                    text='{"summary": "涉及 Nebula 与 CR-2 审批链。"}', prompt_tokens=1, completion_tokens=1
                )
            return LLMResponse(text=extraction_for(user), prompt_tokens=1, completion_tokens=1)

    return ContentScriptedLLM()


@pytest.fixture
def built_artifacts(tmp_path: Path, corpus_dir: Path, extraction_llm: ScriptedLLM) -> Path:
    artifacts = tmp_path / "artifacts"
    build_index(
        corpus_dir,
        artifacts,
        llm=extraction_llm,
        embedder=HashingEmbedder(dim=64),
        workers=2,
        progress=lambda message: None,
    )
    return artifacts


@pytest.fixture
def index(built_artifacts: Path):
    return load_retriever_index(built_artifacts, embedder=HashingEmbedder(dim=64))


@pytest.fixture
def graph(corpus_dir: Path, extraction_llm: ScriptedLLM) -> KnowledgeGraph:
    from graphrag_mvp.corpus import load_corpus

    chunks = load_corpus(corpus_dir)
    extractions = extract_chunks(chunks, extraction_llm, cache=None, workers=2)
    return KnowledgeGraph.from_extractions(extractions.values())


@pytest.fixture
def bm25(corpus_dir: Path) -> BM25Index:
    from graphrag_mvp.corpus import load_corpus

    return BM25Index(load_corpus(corpus_dir))


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dim=64)


@pytest.fixture
def empty_store(tmp_path: Path) -> EmbeddingStore:
    return EmbeddingStore(path=tmp_path / "embeddings.jsonl")
