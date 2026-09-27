"""`wiki-rag-eval`: 판정 실험용 인덱스를 만들고, 구성별로 골든셋을 돌리고, ADR-20 규칙으로 판정한다.

    wiki-rag-eval index noheader        # 맥락 헤더를 뺀 비교용 인덱스
    wiki-rag-eval run baseline          # 구성 하나를 골든셋 전체로 돌린다
    wiki-rag-eval report                # 돌린 구성들의 지표와 지연
    wiki-rag-eval judge baseline vector-rerank --metric mrr@10 --threshold 0.05
"""

import argparse
import json
from pathlib import Path

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.evaluation.run import CONFIGS, INDEXES

GOLDEN = Path("data/golden/golden.jsonl")
RUNS = Path("experiments/m2-judgement/runs")


def _index(name: str) -> None:
    from wiki_rag_mcp.indexing.embedder import Embedder, TokenCounter
    from wiki_rag_mcp.indexing.indexer import index_all
    from wiki_rag_mcp.search.backend import open_store
    from wiki_rag_mcp.wiki.files import FileWikiSource

    spec = INDEXES[name]
    settings = Settings.from_env()
    store = open_store(settings, spec.alias)
    store.drop()
    embedder = Embedder()  # 문서 임베딩은 같은 모델·형식이면 장치와 관계없이 같아, 빠른 장치(MPS)를 쓴다
    index_name = store.ensure_index()
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
    judge_p = sub.add_parser("judge")
    judge_p.add_argument("base")
    judge_p.add_argument("candidate")
    judge_p.add_argument("--metric", default="recall@5", choices=["recall@5", "mrr@10", "ndcg@10"])
    judge_p.add_argument("--threshold", type=float, required=True)
    args = parser.parse_args(argv)

    if args.command == "index":
        _index(args.name)
    elif args.command == "run":
        from wiki_rag_mcp.evaluation.run import Runner, run
        from wiki_rag_mcp.indexing.embedder import Embedder
        from wiki_rag_mcp.search.backend import open_store
        from wiki_rag_mcp.wiki.files import FileWikiSource

        config = CONFIGS[args.name]
        settings = Settings.from_env()
        golden = [json.loads(line) for line in args.golden.read_text(encoding="utf-8").splitlines()]
        embedder = Embedder(device="cpu")  # 배포 서버처럼 CPU에서 질문을 임베딩한다
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
    else:
        from wiki_rag_mcp.evaluation.judge import verdict
        from wiki_rag_mcp.evaluation.report import load_run, paired

        c = paired(load_run(RUNS / f"{args.base}.jsonl"), load_run(RUNS / f"{args.candidate}.jsonl"), args.metric)
        print(f"{args.metric}: {args.base} {c.base:.3f}, {args.candidate} {c.candidate:.3f}, "
              f"차이 {c.diff:+.3f} (95% 신뢰구간 {c.low:+.3f} ~ {c.high:+.3f}), 문항 {c.n}개, "
              f"결과가 다른 문항 {c.discordant:.1%}, 기준값 {args.threshold:+.3f} -> {verdict(c, args.threshold)}")


if __name__ == "__main__":
    main()
