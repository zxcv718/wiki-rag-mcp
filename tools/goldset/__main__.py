import argparse
import sys
from pathlib import Path

import yaml

from tools.goldset.select import load_sources, select, write_sources
from tools.wikigen.spec import load_spec

WIKI = Path("data/wiki")
GOLDEN = Path("data/golden")
PLANTS = Path("tools/wikigen/plants.yaml")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.goldset", description="골든셋 만들기")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("select", help="유형별 출처와 질문자를 고른다 (LLM 사용 안 함)")
    sub.add_parser("write", help="출처마다 질문을 쓴다 (LLM 사용). 이미 쓴 문항은 건너뛴다")
    blind = sub.add_parser("blind", help="출처를 뺀 질문 목록을 블라인드 라벨러용으로 나눠 내보낸다")
    blind.add_argument("--parts", type=int, default=4)
    blind.add_argument("--out", type=Path, required=True, help="내보낼 디렉터리 (골든셋 디렉터리 밖)")
    merge = sub.add_parser("merge", help="출처 라벨과 블라인드 라벨을 합쳐 골든셋을 만든다")
    merge.add_argument("--blind", type=Path, default=GOLDEN / "blind_labels.jsonl")
    args = parser.parse_args()

    spec = load_spec(WIKI / "schema.yaml", PLANTS)
    sources_path = GOLDEN / "sources.yaml"
    if args.command == "select":
        manifest = yaml.safe_load((WIKI / "manifest.yaml").read_text(encoding="utf-8"))
        write_sources(sources_path, select(spec, manifest))
        print(f"출처 작성: {sources_path}")
        return 0

    if args.command in ("blind", "merge"):
        import json

        from tools.goldset.labels import export_blind, merge

        questions = {r["id"]: r["question"] for r in map(json.loads, (GOLDEN / "questions.jsonl").open())}
        sources = load_sources(sources_path)
        if args.command == "blind":
            for path in export_blind(spec, sources, questions, args.parts, args.out, spec.schema["seed"]):
                print(f"내보냄: {path}")
            return 0
        manifest = yaml.safe_load((WIKI / "manifest.yaml").read_text(encoding="utf-8"))
        blind = {r["id"]: r for r in map(json.loads, args.blind.open())}
        overrides_path = GOLDEN / "overrides.yaml"
        overrides = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {} if overrides_path.exists() else {}
        golden, review = merge(spec, manifest, sources, questions, blind, overrides)
        (GOLDEN / "golden.jsonl").write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in golden),
                                             encoding="utf-8")
        (GOLDEN / "review_queue.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"골든셋 {len(golden)}문항, 검토 대기 {len(review)}문항")
        return 1 if review else 0

    from tools.goldset.write import run_write
    from tools.wikigen.llm import LLM, usage_summary

    log = GOLDEN / "generation_log.jsonl"
    failed = run_write(spec, load_sources(sources_path), GOLDEN / "questions.jsonl", LLM(log))
    usage = usage_summary(log)
    print(f"누적 호출 {usage['calls']}회, "
          f"입력 {usage['prompt_tokens']:,} 토큰, 출력 {usage['completion_tokens']:,} 토큰")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
