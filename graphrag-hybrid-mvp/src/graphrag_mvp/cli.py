"""统一命令行入口。

    graphrag-mvp build  [--corpus DIR] [--artifacts DIR] [--embedder onnx|hashing] [--workers N]
    graphrag-mvp ask    "问题" [--channels lexical,dense,graph,community] [--top-k N] [--evidence]
    graphrag-mvp eval   [--answers] [--out DIR]            # 通道消融；--answers 追加答案级评分
    graphrag-mvp stats                                      # 索引规模 + 图谱/社区概览
    graphrag-mvp search "问题" [--channel lexical]          # 单通道原始命中（调参用）

`--llm scripted` 提供离线桩，使建索引/问答/评测在无网络环境下也能走完整链路（CI 用）。
所有产物路径都可由参数覆盖，便于「输出隔离」：`--out runs/<timestamp>` 不覆盖历史产物。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from .answer import answer_question, format_evidence, summarize_communities_for_display
from .config import CHANNEL_NAMES, DEFAULT_WEIGHTS, Settings, parse_weights
from .embedding import HashingEmbedder, OnnxEmbedder
from .evaluate import evaluate as run_eval
from .evaluate import load_questions, score_answer
from .graph import KnowledgeGraph
from .llm import DeepSeekClient, ScriptedLLM
from .pipeline import build_index, load_retriever_index
from .retriever import ALL_CHANNELS, retrieve


def _build_llm(settings: Settings, name: str):
    if name == "scripted":
        return ScriptedLLM(
            default=json.dumps(
                {
                    "entities": [
                        {"name": "Nebula", "type": "系统", "aliases": ["Nebula 平台"]},
                        {"name": "CR-2", "type": "流程", "aliases": ["高风险变更"]},
                    ],
                    "relations": [{"source": "CR-2", "predicate": "审批", "target": "Nebula"}],
                },
                ensure_ascii=False,
            )
        )
    return DeepSeekClient(
        api_key=settings.require_api_key(),
        base_url=settings.base_url,
        model=settings.llm_model,
        cache_dir=settings.cache_dir / "llm",
    )


def _build_embedder(settings: Settings, name: str):
    if name == "hashing":
        return HashingEmbedder()
    return OnnxEmbedder(
        model_name=settings.embed_model,
        cache_dir=settings.cache_dir / "models",
        query_prefix=settings.query_prefix,
    )


def _resolve_channels(spec: str | None) -> list[str]:
    if not spec:
        return list(ALL_CHANNELS)
    channels = []
    for name in spec.replace(" ", "").split(","):
        if name and name not in CHANNEL_NAMES:
            raise SystemExit(f"未知通道 {name!r}，可选：{', '.join(CHANNEL_NAMES)}")
        if name:
            channels.append(name)
    return channels


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- 子命令
def cmd_build(args: argparse.Namespace) -> int:
    settings = Settings.load(artifacts_dir=Path(args.artifacts))
    llm = _build_llm(settings, args.llm)
    embedder = _build_embedder(settings, args.embedder)
    report = build_index(
        Path(args.corpus),
        Path(args.artifacts),
        llm=llm,
        embedder=embedder,
        workers=args.workers,
        progress=lambda message: print(f"  {message}"),
    )
    _print_json(report.to_dict())
    return 0


def _load_index(args: argparse.Namespace, settings: Settings, channels: list[str]):
    embedder = _build_embedder(settings, args.embedder) if "dense" in channels else None
    return load_retriever_index(settings.artifacts_dir, embedder=embedder, lexical_mode=settings.lexical_mode)


def cmd_ask(args: argparse.Namespace) -> int:
    settings = Settings.load(
        artifacts_dir=Path(args.artifacts), top_k=args.top_k, channel_floor=args.channel_floor
    )
    channels = _resolve_channels(args.channels)
    index = _load_index(args, settings, channels)
    weights = parse_weights(args.weights) if args.weights else dict(DEFAULT_WEIGHTS)
    result = retrieve(
        index,
        args.question,
        top_k=args.top_k,
        channels=channels,
        weights=weights,
        graph_hops=settings.graph_hops,
        channel_floor=settings.channel_floor,
    )
    llm = _build_llm(settings, args.llm)
    answer = answer_question(args.question, result, llm)
    if args.json:
        _print_json(answer.to_dict())
        return 0

    print(f"\n问题：{args.question}")
    print(
        f"通道：{'、'.join(channels)}  权重：{weights}  "
        f"保底席位：{settings.channel_floor}  图谱跳数：{settings.graph_hops}"
    )
    print("\n答案：")
    print(f"  {answer.text}")
    print(f"\n引用文档：{', '.join(answer.citations) or '（无）'}")
    tokens_prompt = answer.usage.get("prompt_tokens", 0)
    tokens_completion = answer.usage.get("completion_tokens", 0)
    print(f"Token：prompt={tokens_prompt} completion={tokens_completion}")
    if args.evidence:
        print("\n检索证据：")
        print(format_evidence(result, limit=args.top_k))
        if result.communities:
            print("\n命中的话题簇：")
            print(summarize_communities_for_display(result.communities))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    settings = Settings.load(artifacts_dir=Path(args.artifacts))
    embedder = _build_embedder(settings, args.embedder) if args.channel == "dense" else None
    index = load_retriever_index(
        settings.artifacts_dir, embedder=embedder, lexical_mode=settings.lexical_mode
    )
    result = retrieve(
        index,
        args.question,
        top_k=args.top_k,
        channels=[args.channel],
        per_channel_k=max(args.top_k, 10),
    )
    print(f"\n单通道 {args.channel} 命中（{args.question}）：")
    for hit in result.hits:
        chunk = index.chunk(hit.chunk_id)
        title = chunk.doc_title if chunk else "?"
        print(f"  {hit.rank}. {hit.chunk_id} 《{title}》 score={hit.score:.6f}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    settings = Settings.load(artifacts_dir=Path(args.artifacts), channel_floor=args.channel_floor)
    channels = _resolve_channels(args.channels)
    index = _load_index(args, settings, channels)
    questions = load_questions(Path(args.questions))
    weights = parse_weights(args.weights) if args.weights else dict(DEFAULT_WEIGHTS)
    report = run_eval(
        index,
        questions,
        top_k=args.top_k,
        graph_hops=settings.graph_hops,
        channel_floor=settings.channel_floor,
        weights=weights,
    )

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "eval.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "eval.md").write_text("# 通道消融结果\n\n" + report.to_markdown() + "\n", encoding="utf-8")

    print(f"\n检索评测（{len(questions)} 题，top_k={args.top_k}）：\n")
    print(report.to_markdown())

    if args.answers:
        llm = _build_llm(settings, args.llm)
        rows: list[dict[str, Any]] = []
        for record in questions:
            result = retrieve(
                index,
                record.question,
                top_k=args.top_k,
                channels=channels,
                weights=weights,
                graph_hops=settings.graph_hops,
                channel_floor=settings.channel_floor,
            )
            answer = answer_question(record.question, result, llm)
            rows.append(score_answer(record, answer.text, answer.citations).to_dict())
        keyword = statistics.fmean([float(row["keyword_coverage"]) for row in rows]) if rows else 0.0
        citation = statistics.fmean([float(row["citation_recall"]) for row in rows]) if rows else 0.0
        print(f"\n答案级评分（LLM={settings.llm_model}，通道={'、'.join(channels)}）：")
        print(f"  关键词覆盖 {keyword:.3f}，引用召回 {citation:.3f}")
        if out_dir:
            with (out_dir / "answers.jsonl").open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            (out_dir / "answers_summary.json").write_text(
                json.dumps(
                    {
                        "channels": channels,
                        "llm": settings.llm_model,
                        "top_k": args.top_k,
                        "keyword_coverage": round(keyword, 4),
                        "citation_recall": round(citation, 4),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    if out_dir:
        print(f"\n产物目录：{out_dir}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = Settings.load(artifacts_dir=Path(args.artifacts))
    index = load_retriever_index(settings.artifacts_dir, embedder=None, lexical_mode=settings.lexical_mode)
    graph = index.graph or KnowledgeGraph()
    communities = index.communities
    payload = {
        "artifacts_dir": str(settings.artifacts_dir),
        "chunks": len(index.chunks),
        "docs": len({chunk.doc_id for chunk in index.chunks}),
        "graph": graph.stats(),
        "communities": len(communities),
        "top_entities": sorted(
            ((entity.name, len(entity.chunk_ids)) for entity in graph.entities.values()),
            key=lambda item: (-item[1], item[0]),
        )[:10],
    }
    _print_json(payload)
    if communities:
        print("\n话题簇摘要：")
        print(summarize_communities_for_display(communities[:5]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphrag-mvp", description=__doc__)
    defaults = Settings()
    parser.add_argument("--artifacts", default=str(defaults.artifacts_dir), help="索引产物目录")
    parser.add_argument(
        "--embedder", default="onnx", choices=["onnx", "hashing"], help="向量实现（hashing 为离线降级）"
    )
    parser.add_argument("--llm", default="deepseek", choices=["deepseek", "scripted"])
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="构建四通道索引")
    build.add_argument("--corpus", default=str(defaults.corpus_dir))
    build.add_argument("--workers", type=int, default=defaults.workers)
    build.set_defaults(func=cmd_build)

    ask = subparsers.add_parser("ask", help="混合检索并生成带引用的答案")
    ask.add_argument("question")
    ask.add_argument("--channels", default=None, help="逗号分隔：lexical,dense,graph,community")
    ask.add_argument("--weights", default=None, help="如 graph:1.5,lexical:1.0")
    ask.add_argument("--top-k", type=int, default=defaults.top_k)
    ask.add_argument(
        "--channel-floor", type=int, default=defaults.channel_floor, help="每通道在上下文中的保底席位数"
    )
    ask.add_argument("--evidence", action="store_true", help="打印各通道命中与话题簇")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=cmd_ask)

    search = subparsers.add_parser("search", help="单通道原始命中")
    search.add_argument("question")
    search.add_argument("--channel", default="lexical", choices=list(CHANNEL_NAMES))
    search.add_argument("--top-k", type=int, default=10)
    search.set_defaults(func=cmd_search)

    evaluation = subparsers.add_parser("eval", help="通道消融评测")
    evaluation.add_argument("--questions", default=str(defaults.eval_dir / "questions.jsonl"))
    evaluation.add_argument("--channels", default=None)
    evaluation.add_argument("--weights", default=None, help="如 graph:1.5,lexical:1.0")
    evaluation.add_argument("--top-k", type=int, default=defaults.top_k)
    evaluation.add_argument("--channel-floor", type=int, default=defaults.channel_floor)
    evaluation.add_argument("--answers", action="store_true", help="额外跑 LLM 答案级评分（需 API）")
    evaluation.add_argument("--out", default=None, help="结果输出目录（输出隔离：建议 runs/<ts>）")
    evaluation.set_defaults(func=cmd_eval)

    stats = subparsers.add_parser("stats", help="索引与图谱概览")
    stats.set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
