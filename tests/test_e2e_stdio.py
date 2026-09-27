"""MCP 서버를 stdio 하위 프로세스로 띄우고 MCP 클라이언트로 도구를 부른다 (Claude Code가 부르는 방식과 같다).

진짜 bge-m3로 테스트 위키를 색인한 뒤, 사용자를 바꿔 가며 권한 결과를 확인한다. 모델을 불러와서 느리다.
"""

import json
import os
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.pg import new_store
from wiki_rag_mcp.indexing.embedder import Embedder, TokenCounter
from wiki_rag_mcp.indexing.indexer import index_all
from wiki_rag_mcp.wiki.files import FileWikiSource

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "wiki"


@pytest.fixture(scope="module")
def alias():
    store = new_store("e2e", 1024)
    index_all(FileWikiSource(FIXTURE), store, Embedder(), TokenCounter())
    yield store.alias
    store.drop()


def call(alias: str, user: str | None, tool: str, args: dict, tier: str = "external"):
    env = {**os.environ, "WIKI_SOURCE": "file", "WIKI_DIR": str(FIXTURE), "WIKI_INDEX_ALIAS": alias,
           "WIKI_CLIENT_TIER": tier, "TOKENIZERS_PARALLELISM": "false"}
    env.pop("WIKI_USER", None)
    if user:
        env["WIKI_USER"] = user
    params = StdioServerParameters(command="uv", args=["run", "--quiet", "wiki-rag-mcp"], env=env, cwd=str(ROOT))

    async def run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(tool, args)
            return tools, result

    return anyio.run(run)


def doc_ids(result) -> list[str]:
    assert not result.is_error, result.content
    payload = result.structured_content or json.loads(result.content[0].text)
    return [r["doc_id"] for r in payload["results"]]


def test_tool_is_listed_as_read_only(alias):
    tools, _ = call(alias, "bob", "search_wiki", {"query": "연차 신청"})
    tool = next(t for t in tools.tools if t.name == "search_wiki")
    assert tool.annotations.read_only_hint is True and tool.annotations.open_world_hint is False


def test_finds_the_revised_remote_work_rule(alias):
    _, result = call(alias, "bob", "search_wiki", {"query": "재택근무 일주일에 몇 번까지 돼?", "top_k": 3})
    ids = doc_ids(result)
    assert "hr-003" in ids
    assert "misc-001" not in ids  # 권한 필드가 빈 문서는 누구에게도 안 나온다


def test_dba_outside_infra_space_cannot_see_restricted_doc(alias):
    _, result = call(alias, "erin", "search_wiki", {"query": "운영 DB 비밀번호 교체 절차", "top_k": 10})
    assert "infra-002" not in doc_ids(result)


def test_member_of_space_and_restriction_can_see_restricted_doc(alias):
    _, result = call(alias, "dana", "search_wiki", {"query": "운영 DB 비밀번호 교체 절차", "top_k": 3})
    assert doc_ids(result)[0] == "infra-002"


def test_partner_sees_only_company_space(alias):
    _, result = call(alias, "kim", "search_wiki", {"query": "연차 휴가", "top_k": 10})
    assert set(doc_ids(result)) <= {"company-001"}


def test_confidential_doc_has_no_snippet_for_external_client(alias):
    _, result = call(alias, "hana", "search_wiki", {"query": "연봉 인상률", "top_k": 3})
    payload = result.structured_content or json.loads(result.content[0].text)
    top = payload["results"][0]
    assert top["doc_id"] == "hr-int-001" and top["snippet"] == ""


def test_missing_user_is_an_error(alias):
    _, result = call(alias, None, "search_wiki", {"query": "연차"})
    assert result.is_error
