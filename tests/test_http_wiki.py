"""HttpWikiSource가 위키 서비스의 응답(wiki-service/README.md)을 제대로 읽는지 가짜 HTTP 전송으로 확인한다.

실제 위키 서비스와의 연동은 wiki-rag-seed와 인덱싱 지연 측정(experiments/m3-indexing)에서 확인한다.
"""

import httpx
import pytest

from wiki_rag_mcp.models import Classification
from wiki_rag_mcp.wiki.http import HttpWikiSource
from wiki_rag_mcp.wiki.source import WikiUnavailableError


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
