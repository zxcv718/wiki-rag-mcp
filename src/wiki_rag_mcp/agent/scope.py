"""서버 안내문과 search_wiki 설명에 범위를 적으면 일반 클라이언트의 도구 선택이 나아지는가.
방법과 판정 기준은 experiments/m6-tool-scope/README.md에 있다.

    python -m wiki_rag_mcp.agent.scope fetch      운영 서버의 안내문과 도구 정의를 저장한다 (A)
    python -m wiki_rag_mcp.agent.scope run --model gpt-5.4 --prompt generic --set new --repeat 3
    python -m wiki_rag_mcp.agent.scope report

모델에는 도구 정의만 주고 첫 응답에서 고른 도구를 본다. 도구를 실제로 부르지 않으므로 run에는 서버가 필요 없다.
"""

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import anyio
import yaml

from wiki_rag_mcp.agent.cli import DEFAULT_SERVER
from wiki_rag_mcp.agent.connect import BearerToken, connect
from wiki_rag_mcp.agent.core import SYSTEM_PROMPT, openai_tools
from wiki_rag_mcp.agent.evaluate import EXPERIMENT as AGENT_EXPERIMENT
from wiki_rag_mcp.agent.evaluate import Tokens, append_jsonl, read_jsonl, write_meta
from wiki_rag_mcp.agent.llm import ChatModel
from wiki_rag_mcp.evaluation.judge import compare, verdict

EXPERIMENT = Path("experiments/m6-tool-scope")
RUNS = EXPERIMENT / "runs"
WIKI_TOOLS = {"search_wiki", "get_document", "list_recent_changes"}
CONCURRENCY = 4
PRIMARY = ("gpt-5.4", "generic", "new")  # 주 판정의 (모델, 프롬프트, 질문 세트)
REGRESSION = ("gpt-5.4", "demo", "old")
THRESHOLD = 0.10  # 회사 질문 차이의 기준값, 일반 질문과 데모 에이전트 오류율의 상한. README "판정 기준"

# 회사 이야기가 없는 일반 클라이언트. 서버 안내문을 시스템 프롬프트에 붙이는 클라이언트를 흉내 낸다
GENERIC_PROMPT = """당신은 사용자를 돕는 AI 도우미입니다. 오늘은 {today}입니다. 필요하면 연결된 도구를 쓸 수 있습니다.

연결된 MCP 서버(wiki-rag)의 안내:
{instructions}"""
PROMPTS = {"generic": GENERIC_PROMPT, "demo": SYSTEM_PROMPT}


def definitions(base: dict[str, Any], change: dict[str, str] | None) -> tuple[str, list[dict[str, Any]]]:
    """(안내문, 도구 정의). change가 있으면 B: 안내문과 search_wiki 설명만 바꾼다."""
    if change is None:
        return base["instructions"], base["tools"]
    tools = [{**t, "function": {**t["function"], "description": change["search_wiki"]}}
             if t["function"]["name"] == "search_wiki" else t for t in base["tools"]]
    return change["instructions"], tools


def load_definitions(condition: str) -> tuple[str, list[dict[str, Any]]]:
    base = json.loads((EXPERIMENT / "tools.A.json").read_text(encoding="utf-8"))
    change = yaml.safe_load((EXPERIMENT / "variant_b.yaml").read_text(encoding="utf-8")) if condition == "B" else None
    return definitions(base, change)


def questions(which: str) -> list[dict[str, Any]]:
    return read_jsonl(EXPERIMENT / "questions.jsonl" if which == "new" else AGENT_EXPERIMENT / "tool_questions.jsonl")


def run_file(model: str, prompt: str, which: str) -> Path:
    return RUNS / f"{model}.{prompt}.{which}.jsonl"


def _key(r: dict[str, Any]) -> tuple[str, int, str]:
    return r["condition"], r["rep"], r["id"]


async def fetch(args: argparse.Namespace) -> None:
    async with connect(args.server, BearerToken(Tokens(args.token_command).get("jiho"))) as client:
        tools = openai_tools((await client.list_tools()).tools)
        data = {"server": args.server, "instructions": client.instructions, "tools": tools}
    (EXPERIMENT / "tools.A.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{EXPERIMENT / 'tools.A.json'}: 도구 {len(tools)}개")


async def run(args: argparse.Namespace) -> None:
    out = run_file(args.model, args.prompt, args.set)
    done = {_key(r) for r in read_jsonl(out)} if out.exists() else set()
    jobs = []
    for condition in args.conditions:
        instructions, tools = load_definitions(condition)
        system = PROMPTS[args.prompt].format(today=date.today().isoformat(), instructions=instructions)
        for rep in range(args.repeat):
            for q in questions(args.set):
                record = {**q, "condition": condition, "rep": rep}
                if _key(record) not in done:
                    jobs.append((record, system, tools))
    # gpt-5.4는 M6 측정과 같게 온도 0, 다른 모델은 제공자 기본값
    llm = ChatModel(args.model, temperature=0.0 if args.model.startswith("gpt") else None)
    limiter = anyio.Semaphore(CONCURRENCY)

    async def one(record: dict[str, Any], system: str, tools: list[dict[str, Any]]) -> None:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": record["question"]}]
        async with limiter:
            message = await llm.complete(messages, tools=tools)
        chosen = [c["function"]["name"] for c in message.get("tool_calls") or []]
        append_jsonl(out, {**record, "chosen": chosen, "content": (message.get("content") or "")[:200]})

    async with anyio.create_task_group() as tg:
        for job in jobs:
            tg.start_soon(one, *job)
    write_meta(out, command="scope run", model=args.model, prompt=args.prompt, set=args.set,
               conditions=args.conditions, repeat=args.repeat, today=date.today().isoformat(), usage=dict(llm.usage))
    await llm.aclose()


# --- 판정 ---------------------------------------------------------------------------------------------------


def called(record: dict[str, Any]) -> bool:
    return bool(WIKI_TOOLS & set(record["chosen"]))


def share_by_question(records: list[dict[str, Any]], condition: str, expected: str) -> dict[str, float]:
    """문항마다 반복한 실행 가운데 맞게 고른 비율. 회사 질문은 위키를 찾음, 일반 질문은 부르지 않음이 맞다."""
    runs: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        if r["condition"] == condition and r["expected"] == expected:
            runs[r["id"]].append(called(r) if expected == "wiki" else not called(r))
    return {qid: sum(v) / len(v) for qid, v in runs.items()}


def paired(records: list[dict[str, Any]], expected: str):
    a, b = share_by_question(records, "A", expected), share_by_question(records, "B", expected)
    ids = sorted(a.keys() & b.keys())
    return compare([a[i] for i in ids], [b[i] for i in ids])


def regression_errors(records: list[dict[str, Any]], condition: str = "B") -> dict[str, float]:
    """데모 에이전트의 M6 도구용 문항에서 의도한 도구마다 첫 도구를 틀린 비율."""
    by_tool: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        if r["condition"] == condition:
            by_tool[r["expected"]].append((r["chosen"][:1] or ["none"])[0] != r["expected"])
    return {tool: sum(v) / len(v) for tool, v in by_tool.items()}


def decide(primary: list[dict[str, Any]], regression: list[dict[str, Any]]) -> dict[str, Any]:
    company, general = paired(primary, "wiki"), paired(primary, "none")
    company_verdict = verdict(company, THRESHOLD)
    general_ok = general.low >= -THRESHOLD and 1 - general.candidate <= THRESHOLD
    errors = regression_errors(regression)
    regression_ok = bool(errors) and all(e <= THRESHOLD for e in errors.values())
    if company_verdict == "채택" and general_ok and regression_ok:
        final = "채택"
    elif company_verdict == "기각" or not general_ok or not regression_ok:
        final = "기각"
    else:
        final = "보류"
    return {"company": company, "company_verdict": company_verdict, "general": general, "general_ok": general_ok,
            "regression": errors, "regression_ok": regression_ok, "final": final}


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _summary(records: list[dict[str, Any]]) -> list[str]:
    lines = ["| 조건 | 회사 질문: 위키를 찾음 | 일반 질문: 과잉 호출 | 회사 질문에서 search_wiki 아닌 도구 먼저 |",
             "|---|---|---|---|"]
    for condition in sorted({r["condition"] for r in records}):
        rows = [r for r in records if r["condition"] == condition]
        company = [r for r in rows if r["expected"] == "wiki"]
        general = [r for r in rows if r["expected"] == "none"]
        other = sum(1 for r in company if r["chosen"] and r["chosen"][0] != "search_wiki")
        lines.append(f"| {condition} | {sum(map(called, company))}/{len(company)} | "
                     f"{sum(map(called, general))}/{len(general)} | {other} |")
    return lines


def report() -> None:
    lines = []
    for path in sorted(RUNS.glob("*.jsonl")):
        records = read_jsonl(path)
        lines += [f"## {path.stem}", ""]
        if path.stem.endswith(".old"):
            for condition in sorted({r["condition"] for r in records}):
                errors = regression_errors(records, condition)
                lines.append(f"- {condition}: " + ", ".join(f"{t} {_pct(e)}" for t, e in sorted(errors.items())))
        else:
            lines += _summary(records)
        lines.append("")
    primary, regression = run_file(*PRIMARY), run_file(*REGRESSION)
    if primary.exists() and regression.exists():
        d = decide(read_jsonl(primary), read_jsonl(regression))
        c, g = d["company"], d["general"]
        errors = ", ".join(f"{t} {_pct(e)}" for t, e in sorted(d["regression"].items()))
        lines += ["## 주 판정", "",
                  f"- 회사 질문, 위키를 찾은 비율: A {_pct(c.base)}, B {_pct(c.candidate)}, 차이 {_pct(c.diff)} "
                  f"(95% 구간 {_pct(c.low)} ~ {_pct(c.high)}), 문항 {c.n}개: {d['company_verdict']}",
                  f"- 일반 질문, 부르지 않은 비율: A {_pct(g.base)}, B {_pct(g.candidate)}, 차이 {_pct(g.diff)} "
                  f"(95% 구간 {_pct(g.low)} ~ {_pct(g.high)}): {'만족' if d['general_ok'] else '불만족'}",
                  f"- 데모 에이전트(B) 오류율: {errors}: {'만족' if d['regression_ok'] else '불만족'}",
                  f"- 판정: **{d['final']}**"]
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="서버가 주는 범위와 도구 선택 (experiments/m6-tool-scope)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--server", default=DEFAULT_SERVER)
    p.add_argument("--token-command", default=str(AGENT_EXPERIMENT / "token.sh"))
    p = sub.add_parser("run")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", choices=sorted(PROMPTS), required=True)
    p.add_argument("--set", choices=["new", "old"], required=True, help="new: 이 실험의 40문항, old: M6 도구용 50문항")
    p.add_argument("--conditions", nargs="+", choices=["A", "B"], default=["A", "B"])
    p.add_argument("--repeat", type=int, default=1)
    sub.add_parser("report")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        anyio.run(fetch, args)
    elif args.command == "run":
        RUNS.mkdir(parents=True, exist_ok=True)
        anyio.run(run, args)
    else:
        report()


if __name__ == "__main__":
    main()
