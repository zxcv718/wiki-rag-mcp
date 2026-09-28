"""가상 위키를 Spring 위키로 옮길 때 권한 의미가 바뀌지 않는지 확인한다.

옮기는 형식은 wiki-service/README.md의 POST /admin/import다.
"""

import json
from pathlib import Path

import httpx
import pytest

from wiki_rag_mcp.wiki.files import FileWikiSource
from wiki_rag_mcp.wiki.seed import import_payload, seed

FIXTURE = Path(__file__).parent / "fixtures" / "wiki"


def test_empty_permission_lists_stay_empty():
    """빈 목록은 아무도 볼 수 없다는 뜻이다. 옮기다가 기본값(all)으로 채워지면 누출이 된다."""
    payload = import_payload(FIXTURE)
    source = FileWikiSource(FIXTURE)
    docs = {d["doc_id"]: d for d in payload["documents"]}
    for doc in source.documents():
        assert docs[doc.doc_id]["restricted_principals"] == list(doc.restricted_principals)
    spaces = {s["space"]: s for s in payload["spaces"]}
    for doc in source.documents():
        assert spaces[doc.space]["principals"] == list(doc.space_principals)
    assert any(not d["restricted_principals"] or not spaces[d["space"]]["principals"] for d in docs.values())


def test_document_classification_is_kept_only_when_above_the_space_default():
    payload = import_payload(FIXTURE)
    spaces = {s["space"]: s["classification"] for s in payload["spaces"]}
    for doc in FileWikiSource(FIXTURE).documents():
        own = next(d for d in payload["documents"] if d["doc_id"] == doc.doc_id)["classification"]
        # 위키는 max(스페이스 기본, 문서 등급)을 쓰므로, 옮긴 뒤의 최종 등급이 같아야 한다
        assert (own or spaces[doc.space]) == str(doc.classification)
        assert own is None or own != spaces[doc.space]


def test_versions_and_revisions_are_carried_over():
    payload = import_payload(FIXTURE)
    docs = {d["doc_id"]: d for d in payload["documents"]}
    for doc in FileWikiSource(FIXTURE).documents():
        assert (docs[doc.doc_id]["version"], docs[doc.doc_id]["revision"]) == (doc.version, doc.revision)


def wiki_recording(import_status=200):
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(import_status if request.url.path == "/admin/import" else 204)

    return httpx.Client(base_url="http://wiki.test", transport=httpx.MockTransport(handler)), requests


def test_demo_password_is_set_for_every_imported_user():
    payload = import_payload(FIXTURE)
    wiki, requests = wiki_recording()
    seed(wiki, payload, "correct-horse-battery")
    assert (requests[0].method, requests[0].url.path) == ("POST", "/admin/import")
    puts = requests[1:]
    assert all(r.method == "PUT" and json.loads(r.content) == {"password": "correct-horse-battery"} for r in puts)
    assert sorted(r.url.path for r in puts) == sorted(f"/admin/users/{u['user_id']}/password" for u in payload["users"])


def test_without_password_only_imports():
    wiki, requests = wiki_recording()
    seed(wiki, import_payload(FIXTURE), None)
    assert [(r.method, r.url.path) for r in requests] == [("POST", "/admin/import")]


def test_short_password_is_refused_before_importing():
    """옮긴 뒤에 실패하면 위키가 비어 있지 않게 되어 다시 옮길 수 없다. 옮기기 전에 막는다."""
    wiki, requests = wiki_recording()
    with pytest.raises(SystemExit):
        seed(wiki, import_payload(FIXTURE), "short-pw")
    assert requests == []


def test_already_seeded_wiki_sets_no_passwords():
    wiki, requests = wiki_recording(import_status=409)
    with pytest.raises(SystemExit):
        seed(wiki, import_payload(FIXTURE), "correct-horse-battery")
    assert [r.method for r in requests] == ["POST"]
