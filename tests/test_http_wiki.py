"""HttpWikiSource가 위키 서비스의 응답(wiki-service/README.md)을 제대로 읽는지 가짜 HTTP 전송으로 확인한다.

실제 위키 서비스와의 연동은 권한 테스트셋(test_permissions_integration.py)과 권한 회수 테스트
(test_wiki_permissions_e2e.py)가 본다.
"""

import httpx
import pytest

from wiki_rag_mcp.models import Classification
from wiki_rag_mcp.wiki.http import HttpWikiSource
from wiki_rag_mcp.wiki.source import GroupLookupError, UnknownUserError, WikiUnavailableError


def state(doc_id, **extra):
    return {"doc_id": doc_id, "title": f"{doc_id} 제목", "space": "infra", "url": f"https://wiki.test/doc/{doc_id}",
            "version": 2, "revision": 5, "updated_at": "2026-09-01T02:00:00Z", "body": "# 제목\n본문",
            "space_principals": ["group:eng", "group:infra"], "restricted_principals": ["group:dba"],
            "classification": "confidential", "deleted": False, **extra}


def source_with(handler):
    requests: list[httpx.Request] = []

    def record(request):
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return HttpWikiSource("http://wiki.test/", "service-token", client=client), requests


def test_reads_a_live_document_with_the_service_token():
    source, requests = source_with(lambda r: httpx.Response(200, json=state("infra-011")))
    result = source.state("infra-011")
    assert requests[0].url == "http://wiki.test/internal/documents/infra-011"
    assert requests[0].headers["authorization"] == "Bearer service-token"
    doc = result.document
    assert (result.revision, doc.version, doc.classification) == (5, 2, Classification.CONFIDENTIAL)
    assert doc.restricted_principals == ("group:dba",)
    assert doc.updated_at.isoformat() == "2026-09-01T02:00:00+00:00"


def test_deleted_and_never_existing_documents():
    def handler(request):
        if request.url.path.endswith("/gone"):
            return httpx.Response(200, json={"doc_id": "gone", "revision": 7, "deleted": True})
        return httpx.Response(404, json={"error": "not_found", "message": "없음"})

    source, _ = source_with(handler)
    gone = source.state("gone")
    assert (gone.revision, gone.deleted) == (7, True)
    never = source.state("never")
    assert (never.revision, never.deleted) == (None, True)


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(401)])
def test_errors_are_not_mistaken_for_missing_documents(response):
    """404가 아닌 오류를 "없는 문서"로 읽으면 인덱서가 멀쩡한 문서를 지운다."""
    source, _ = source_with(lambda r: response)
    with pytest.raises(WikiUnavailableError):
        source.state("infra-011")


def test_connection_failure_is_wiki_unavailable():
    def handler(request):
        raise httpx.ConnectError("연결 거부")

    source, _ = source_with(handler)
    with pytest.raises(WikiUnavailableError):
        source.revisions()


@pytest.mark.parametrize("doc_id", ["../admin/import", "..", "a/b", ""])
def test_rejects_doc_ids_that_would_change_the_path(doc_id):
    source, requests = source_with(lambda r: httpx.Response(200, json=state("x")))
    with pytest.raises(ValueError):
        source.state(doc_id)
    assert requests == []


def test_documents_follow_pages():
    pages = {None: (["a", "b"], "b"), "b": (["c"], None)}

    def handler(request):
        ids, nxt = pages[request.url.params.get("after")]
        return httpx.Response(200, json={"documents": [state(i) for i in ids], "next": nxt})

    source, requests = source_with(handler)
    assert [d.doc_id for d in source.documents()] == ["a", "b", "c"]
    assert len(requests) == 2


def test_space_titles_are_read_once():
    spaces = {"spaces": [{"space": "infra", "title": "인프라"}]}
    source, requests = source_with(lambda r: httpx.Response(200, json=spaces))
    assert source.space_title("infra") == "인프라"
    assert source.space_title("unknown") == "unknown"
    assert len(requests) == 1


def test_revisions_include_tombstones():
    body = {"documents": [{"doc_id": "a", "revision": 3, "deleted": False},
                          {"doc_id": "b", "revision": 9, "deleted": True}]}
    source, _ = source_with(lambda r: httpx.Response(200, json=body))
    assert [(e.doc_id, e.revision, e.deleted) for e in source.revisions()] == [("a", 3, False), ("b", 9, True)]


def test_groups_of_reads_group_principals():
    body = {"user_id": "jiho", "groups": ["group:employees", "group:eng"]}
    source, requests = source_with(lambda r: httpx.Response(200, json=body))
    assert source.groups_of("jiho") == ["group:employees", "group:eng"]
    assert requests[0].url == "http://wiki.test/internal/users/jiho/groups"
    assert requests[0].headers["authorization"] == "Bearer service-token"


def test_unknown_user_is_not_a_user_without_groups():
    source, _ = source_with(lambda r: httpx.Response(404, json={"error": "not_found", "message": "없음"}))
    with pytest.raises(UnknownUserError):
        source.groups_of("stranger")


@pytest.mark.parametrize("response", [
    httpx.Response(500), httpx.Response(401), httpx.Response(200, text="not json"),
    httpx.Response(200, json={"user_id": "jiho", "groups": ["user:ceo"]}),  # 그룹 자리에 다른 사용자
    httpx.Response(200, json={"user_id": "jiho", "groups": ["all"]}),
    httpx.Response(200, json={"user_id": "someone-else", "groups": []}),
    httpx.Response(200, json={"user_id": "jiho"}),
    httpx.Response(200, json=["group:eng"]),
])
def test_group_responses_outside_the_contract_are_lookup_failures(response):
    """계약과 다른 그룹 응답을 추측해서 읽지 않는다. 읽지 못한 것으로 보고 요청 전체를 실패시킨다 (ADR-08)."""
    source, _ = source_with(lambda r: response)
    with pytest.raises(GroupLookupError):
        source.groups_of("jiho")


def test_group_lookup_connection_failure():
    def handler(request):
        raise httpx.ConnectError("연결 거부")

    source, _ = source_with(handler)
    with pytest.raises(GroupLookupError):
        source.groups_of("jiho")


def test_document_for_asks_the_wiki_with_the_user_id():
    source, requests = source_with(lambda r: httpx.Response(200, json=state("infra-011")))
    doc = source.document_for("infra-011", "taeyang")
    assert requests[0].url == "http://wiki.test/internal/users/taeyang/documents/infra-011"
    assert doc.doc_id == "infra-011" and doc.body == "# 제목\n본문"


def test_document_for_hidden_or_missing_is_none():
    source, _ = source_with(lambda r: httpx.Response(404, json={"error": "not_found", "message": "없음"}))
    assert source.document_for("infra-011", "bob") is None


@pytest.mark.parametrize("response", [
    httpx.Response(500), httpx.Response(200, text="not json"), httpx.Response(200, json={"doc_id": "infra-011"}),
    httpx.Response(200, json=state("other-doc")),
])
def test_document_for_unreadable_responses_are_wiki_unavailable(response):
    """본문 응답을 읽지 못하면 doc_id와 관계없이 같은 오류가 되도록 한 종류의 예외로 모은다."""
    source, _ = source_with(lambda r: response)
    with pytest.raises(WikiUnavailableError):
        source.document_for("infra-011", "taeyang")


@pytest.mark.parametrize(("doc_id", "user_id"), [("..", "bob"), ("ok", ".."), ("ok", "a/b"), ("a/b", "bob"),
                                                 ("ok", "")])
def test_ids_that_would_change_the_path_are_rejected_before_any_request(doc_id, user_id):
    source, requests = source_with(lambda r: httpx.Response(200, json=state("x")))
    with pytest.raises(ValueError):
        source.document_for(doc_id, user_id)
    if doc_id == "ok":
        with pytest.raises(ValueError):
            source.groups_of(user_id)
    assert requests == []
