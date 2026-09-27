"""`wiki-rag-eval`: 판정 실험용 인덱스를 만들고, 구성별로 골든셋을 돌리고, ADR-20 규칙으로 판정한다.

    wiki-rag-eval index noheader        # 맥락 헤더를 뺀 비교용 인덱스
    wiki-rag-eval index gemini          # 상용 임베딩 비교용 인덱스 (GEMINI_API_KEY 필요)
    wiki-rag-eval run baseline          # 구성 하나를 골든셋 전체로 돌린다
    wiki-rag-eval report                # 돌린 구성들의 지표와 지연
    wiki-rag-eval judge baseline hybrid --metric recall@5 --threshold 0.02
"""

import argparse
import json
from pathlib import Path

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.evaluation.run import CONFIGS, INDEXES

GOLDEN = Path("data/golden/golden.jsonl")
RUNS = Path("experiments/m2-judgement/runs")


def _embedder(kind: str):
    if kind == "gemini":
        from wiki_rag_mcp.evaluation.gemini import GeminiEmbedder

        return GeminiEmbedder()
    from wiki_rag_mcp.indexing.embedder import Embedder

    return Embedder(device="cpu")


def _index(name: str) -> None:
    from wiki_rag_mcp.indexing.embedder import TokenCounter
    from wiki_rag_mcp.indexing.indexer import index_all
    from wiki_rag_mcp.search.backend import open_store
    from wiki_rag_mcp.wiki.files import FileWikiSource

    spec = INDEXES[name]
    settings = Settings.from_env()
    store = open_store(settings, spec.alias)
    store.drop()
    if spec.embedder == "gemini":
        embedder = _embedder("gemini")
    else:
        from wiki_rag_mcp.indexing.embedder import Embedder

        embedder = Embedder()  # 문서 임베딩은 같은 모델·형식이면 장치와 관계없이 같아, 빠른 장치(MPS)를 쓴다
    index_name = store.ensure_index(dim=getattr(embedder, "dim", 1024))
    stats = index_all(FileWikiSource(settings.wiki_dir), store, embedder, TokenCounter(), with_header=spec.with_header)
    print(f"색인 완료: {index_name}, 문서 {stats.documents}개, 청크 {stats.chunks}개, {stats.seconds:.1f}초")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="wiki-rag-eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index").add_argument("name", choices=[k for k in INDEXES if k != "main"])
    run_p = sub.add_parser("run")
    run_p.add_argument("name", choices=list(CONFIGS))
    run_p.add_argument("--golden", type=Path, default=GOLDEN)
    run_p.add_argument("--out", type=Path, default=RUNS)
    sub.add_parser("report").add_argument("--runs", type=Path, default=RUNS)
    sub.add_parser("overlap").add_argument("--golden", type=Path, default=GOLDEN)
    judge_p = sub.add_parser("judge")
    judge_p.add_argument("base")
    judge_p.add_argument("candidate")
    judge_p.add_argument("--metric", default="recall@5", choices=["recall@5", "mrr@10", "ndcg@10"])
    judge_p.add_argument("--threshold", type=float, required=True)
    args = parser.parse_args(argv)

    if args.command == "index":
        _index(args.name)
    elif args.command == "run":
        from dataclasses import replace

        from wiki_rag_mcp.evaluation.run import Runner, run
        from wiki_rag_mcp.search.backend import open_store
        from wiki_rag_mcp.wiki.files import FileWikiSource

        config = CONFIGS[args.name]
        settings = replace(Settings.from_env(), search_backend=config.backend)
        golden = [json.loads(line) for line in args.golden.read_text(encoding="utf-8").splitlines()]
        embedder = _embedder(INDEXES[config.index].embedder)
        if hasattr(embedder, "encode_queries"):
            embedder.encode_queries([q["question"] for q in golden])
        reranker = None
        if config.rerank:
            from wiki_rag_mcp.search.reranker import Reranker

            reranker = Reranker(device="cpu")
        runner = Runner(config, open_store(settings, INDEXES[config.index].alias), FileWikiSource(settings.wiki_dir),
                        embedder, reranker)
        print(f"결과: {run(args.name, runner, golden, args.out)}")
    elif args.command == "report":
        from wiki_rag_mcp.evaluation.report import load_run, summary

        print("| 구성 | Recall@5 | MRR@10 | nDCG@10 | p50 ms | p95 ms | 리랭킹 p95 ms |\n|---|---|---|---|---|---|---|")
        for path in sorted(args.runs.glob("*.jsonl")):
            s = summary(load_run(path))
            rerank = f"{s['rerank_p95_ms']:.0f}" if s["rerank_p95_ms"] else "-"
            print(f"| {path.stem} | {s['recall@5']:.3f} | {s['mrr@10']:.3f} | {s['ndcg@10']:.3f} | "
                  f"{s['p50_ms']:.0f} | {s['p95_ms']:.0f} | {rerank} |")
    elif args.command == "overlap":
        import numpy as np

        from wiki_rag_mcp.evaluation.report import lexical_overlap
        from wiki_rag_mcp.search.store import OpenSearchStore
        from wiki_rag_mcp.wiki.files import FileWikiSource

        settings = Settings.from_env()
        store = OpenSearchStore.from_settings(settings)  # 형태소 분석(nori)은 OpenSearch에만 있다

        def analyze(text: str) -> set[str]:
            res = store.client.indices.analyze(index=store.alias, body={"analyzer": "korean", "text": text})
            return {t["token"] for t in res["tokens"]}

        golden = [json.loads(line) for line in args.golden.read_text(encoding="utf-8").splitlines()]
        bodies = {d.doc_id: d.body for d in FileWikiSource(settings.wiki_dir).documents()}
        values = lexical_overlap(golden, analyze, bodies)
        print(f"질문 형태소가 정답 문서에 있는 비율: 평균 {np.mean(values):.1%}, 중앙값 {np.median(values):.1%}, "
              f"문항 {len(values)}개")
    else:
        from wiki_rag_mcp.evaluation.judge import verdict
        from wiki_rag_mcp.evaluation.report import load_run, paired

        c = paired(load_run(RUNS / f"{args.base}.jsonl"), load_run(RUNS / f"{args.candidate}.jsonl"), args.metric)
        print(f"{args.metric}: {args.base} {c.base:.3f}, {args.candidate} {c.candidate:.3f}, "
              f"차이 {c.diff:+.3f} (95% 신뢰구간 {c.low:+.3f} ~ {c.high:+.3f}), 문항 {c.n}개, "
              f"결과가 다른 문항 {c.discordant:.1%}, 기준값 {args.threshold:+.3f} -> {verdict(c, args.threshold)}")


if __name__ == "__main__":
    main()
