"""wiki-rag-demo: 운영 검색 서버에 로그인해, 사내 위키를 근거로 답하는 데모 에이전트.

    uv run wiki-rag-demo "주말 온콜 수당 얼마야?"
    uv run wiki-rag-demo            # 대화 모드. 빈 줄이나 Ctrl-D로 끝낸다

처음 도구를 부를 때 브라우저가 열리고, 위키 계정으로 로그인해 동의하면 이어서 답한다.
"""

import argparse
import json
import sys
from typing import Any

import anyio

from wiki_rag_mcp.agent.connect import LoopbackReceiver, browser_login, connect
from wiki_rag_mcp.agent.core import Agent, Reply, ToolCall, cited_ids, documents_seen
from wiki_rag_mcp.agent.llm import ChatModel

DEFAULT_SERVER = "https://mcp.dmssh.store/mcp"
DEFAULT_MODEL = "gpt-5.4"


def _result_summary(call: ToolCall) -> str:
    if call.is_error:
        return call.text
    data = call.data or {}
    if "results" in data:
        return f"결과 {len(data['results'])}건"
    return f"{data.get('title', '')} ({len(data.get('content', ''))}자)"


def show_call(call: ToolCall) -> None:
    arguments = json.dumps(call.arguments, ensure_ascii=False)
    print(f"  [도구] {call.name} {arguments}  {_result_summary(call)}  {call.seconds:.1f}초", file=sys.stderr)


def show_reply(reply: Reply) -> None:
    print(f"\n{reply.answer}\n")
    seen = documents_seen(reply.calls)
    cited = cited_ids(reply.answer)
    if cited:
        print("출처")
    for doc_id in cited:
        doc: dict[str, Any] | None = seen.get(doc_id)
        if doc:
            print(f"  [{doc_id}] {doc['title']} (버전 {doc['version']}, {str(doc['updated_at'])[:10]} 수정)")
        else:
            print(f"  [{doc_id}] 도구 결과에 없던 문서")
    print()


async def _run(server: str, model: str, question: str | None) -> None:
    receiver = LoopbackReceiver()
    llm = ChatModel(model)
    try:
        async with connect(server, browser_login(server, receiver)) as client:
            agent = Agent(llm, client, on_call=show_call)
            if question:
                show_reply(await agent.ask(question))
                return
            history = None
            while True:
                try:
                    line = (await anyio.to_thread.run_sync(input, "질문> ")).strip()
                except EOFError:
                    break
                if not line:
                    break
                reply = await agent.ask(line, history)
                show_reply(reply)
                history = reply.messages
    finally:
        receiver.close()
        await llm.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="사내 위키 검색 서버(MCP)를 근거로 답하는 데모 에이전트")
    parser.add_argument("question", nargs="?", help="질문. 없으면 대화 모드")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"MCP 엔드포인트 (기본 {DEFAULT_SERVER})")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"COPA 모델 (기본 {DEFAULT_MODEL})")
    args = parser.parse_args(argv)
    anyio.run(_run, args.server, args.model, args.question)
