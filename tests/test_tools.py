"""get_document와 wiki://doc 리소스를 MCP 클라이언트로 부른다 (서버를 같은 프로세스에 띄움, 검색 저장소 불필요).

4장 "검증 방법": 두 층 권한의 두 방향, 빈 권한, 없는 doc_id와 권한 없는 doc_id의 같은 응답,
그룹 조회 실패 시 doc_id와 관계없는 같은 오류, 등급 정책을 도구와 리소스 모두에 적용한다.
"""

import json
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.server.responses import CONFIDENTIAL_NOTE, NOT_FOUND
from wiki_rag_mcp.wiki.files import FileWikiSource
from wiki_rag_mcp.wiki.source import GroupLookupError

FIXTURE = Path(__file__).parent / "fixtures" / "wiki"


class NoEmbedder:
    def encode_query(self, _text):
        raise AssertionError("이 테스트는 검색을 부르지 않는다")


class BrokenGroups(FileWikiSource):
    def groups_of(self, user_id):
        raise GroupLookupError("위키 응답 없음")


def run(user: str | None, action, *, tier: str = "external", source_cls=FileWikiSource):
    services = Services(Settings(wiki_dir=FIXTURE, user=user, client_tier=tier), source_cls(FIXTURE), None,
                        NoEmbedder())

    async def go():
        async with Client(build_server(services)) as client:
            return await action(client)

    return anyio.run(go)


def get(user, doc_id, section=None, **kw):
    args = {"doc_id": doc_id} | ({"section": section} if section else {})
    return run(user, lambda c: c.call_tool("get_document", args), **kw)


def read(user, doc_id, **kw):
    async def action(client):
        try:
            result = await client.read_resource(f"wiki://doc/{doc_id}")
            return json.loads(result.contents[0].text)
        except MCPError as e:
            return e

    return run(user, action, **kw)


def payload(result):
    assert not result.is_error, result.content
    return result.structured_content


def error_text(result):
    """SDK가 붙이는 "Error executing tool ...: " 머리말을 떼고 서버가 보낸 문구만 본다."""
    assert result.is_error
    return result.content[0].text.split(": ", 1)[-1]


def test_three_read_only_tools_and_template_only_resource():
    async def action(client):
        return (await client.list_tools(), await client.list_resources(), await client.list_resource_templates())

    tools, resources, templates = run("bob", action)
    assert {t.name for t in tools.tools} == {"search_wiki", "get_document", "list_recent_changes"}
    assert all(t.annotations.read_only_hint and t.annotations.open_world_hint is False for t in tools.tools)
    assert resources.resources == []  # 문서를 나열하지 않는다
    assert [t.uri_template for t in templates.resource_templates] == ["wiki://doc/{doc_id}"]


def test_member_of_space_and_restriction_reads_restricted_doc():
    doc = payload(get("dana", "infra-002"))
    assert doc["title"] == "운영 DB 비밀번호 교체 절차" and "90일" in doc["content"]
    assert read("dana", "infra-002")["content"] == doc["content"]


@pytest.mark.parametrize("user", ["erin", "bob"])  # 제한 대상이지만 스페이스 밖, 스페이스 안이지만 제한 밖 (ADR-21)
def test_restricted_doc_looks_exactly_like_a_missing_doc(user):
    assert error_text(get(user, "infra-002")) == error_text(get(user, "no-such-doc")) == NOT_FOUND
    denied, missing = read(user, "infra-002"), read(user, "no-such-doc")
    assert isinstance(denied, MCPError) and isinstance(missing, MCPError)
    assert (denied.error.code, denied.error.message) == (missing.error.code, missing.error.message)


def test_empty_permission_doc_is_visible_to_nobody():
    for user in ["bob", "dana", "erin", "hana", "kim"]:
        assert error_text(get(user, "misc-001")) == NOT_FOUND


def test_group_lookup_failure_gives_the_same_error_for_any_doc_id():
    existing = get("dana", "infra-002", source_cls=BrokenGroups)
    missing = get("dana", "no-such-doc", source_cls=BrokenGroups)
    assert error_text(existing) == error_text(missing) != NOT_FOUND
    a, b = read("dana", "infra-002", source_cls=BrokenGroups), read("dana", "no-such-doc", source_cls=BrokenGroups)
    assert (a.error.code, a.error.message) == (b.error.code, b.error.message)


def test_confidential_doc_gives_title_and_link_only_to_external_client():
    doc = payload(get("hana", "hr-int-001"))
    assert doc["content"] == "" and doc["sections"] == [] and CONFIDENTIAL_NOTE in doc["notes"]
    assert doc["title"] and doc["url"]
    assert read("hana", "hr-int-001")["content"] == ""
    assert payload(get("hana", "hr-int-001", tier="internal"))["content"]


def test_section_selection_and_unknown_section():
    doc = payload(get("bob", "hr-003"))
    assert doc["sections"]
    first = doc["sections"][0]
    part = payload(get("bob", "hr-003", section=first))
    assert part["content"].startswith("## ") and len(part["content"]) < len(doc["content"])
    assert first in error_text(get("bob", "hr-003", section="없는 절"))


def test_missing_user_is_an_error():
    assert "WIKI_USER" in error_text(get(None, "hr-003"))
