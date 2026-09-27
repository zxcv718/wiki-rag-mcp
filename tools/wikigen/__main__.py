import argparse
import sys
from pathlib import Path

from tools.wikigen.spec import load_spec

PLANTS = Path(__file__).with_name("plants.yaml")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.wikigen", description="평가용 가상 위키 생성")
    parser.add_argument("--wiki-dir", type=Path, default=Path("data/wiki"))
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="문서 계획을 세워 manifest.yaml을 만든다 (LLM 사용)")
    plan.add_argument("--force", action="store_true", help="manifest.yaml이 있어도 다시 만든다")
    plan.add_argument("--replan", nargs="*", default=[], help="캐시를 버리고 다시 계획할 스페이스")
    write = sub.add_parser("write", help="계획한 문서의 본문을 쓴다 (LLM 사용). 이미 쓴 문서는 건너뛴다")
    write.add_argument("--workers", type=int, default=4)
    write.add_argument("--limit", type=int, help="이번에 쓸 문서 수 상한")
    write.add_argument("--only", nargs="*", help="이 doc_id만 쓴다")
    write.add_argument("--force", action="store_true", help="이미 쓴 문서도 다시 쓴다")
    write.add_argument("--budget", type=int,
                       help="누적 토큰(이전 실행 포함)이 이 값을 넘으면 새 문서를 시작하지 않는다")
    sub.add_parser("lint", help="생성 기록과 문서를 점검한다 (LLM 사용 안 함)")
    args = parser.parse_args()

    wiki_dir: Path = args.wiki_dir
    spec = load_spec(wiki_dir / "schema.yaml", PLANTS)
    log_path = wiki_dir / "generation_log.jsonl"

    if args.command == "lint":
        from tools.wikigen.lint import lint

        problems, stats = lint(spec, wiki_dir)
        print("| 항목 | 값 |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in stats.items()))
        for p in problems:
            print(f"- {p}")
        print(f"\n점검 결과: 문제 {len(problems)}건")
        return 1 if problems else 0

    from tools.wikigen.llm import LLM, usage_summary

    llm = LLM(log_path)
    if args.command == "plan":
        from tools.wikigen.plan import run_plan

        print(f"manifest 작성: {run_plan(spec, wiki_dir, llm, force=args.force, replan=args.replan)}")
        left = 0
    else:
        from tools.wikigen.write import run_write

        left = run_write(spec, wiki_dir, llm, workers=args.workers, limit=args.limit, only=args.only,
                         force=args.force, budget=args.budget)
    usage = usage_summary(log_path)
    print(f"누적 호출 {usage['calls']}회, "
          f"입력 {usage['prompt_tokens']:,} 토큰, 출력 {usage['completion_tokens']:,} 토큰")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
