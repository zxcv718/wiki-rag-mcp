from pathlib import Path

import pytest

from wiki_rag_mcp.auth.principals import principals_for
from wiki_rag_mcp.models import Classification
from wiki_rag_mcp.wiki.files import FileWikiSource
from wiki_rag_mcp.wiki.source import GroupLookupError, UnknownUserError

FIXTURE = Path(__file__).parent / "fixtures" / "wiki"


@pytest.fixture(scope="module")
def source():
    return FileWikiSource(FIXTURE)


def test_loads_all_documents(source):
    assert len(source.documents()) == 11


def test_unrestricted_document_gets_all(source):
    assert source.document("hr-001").restricted_principals == ("all",)


def test_restricted_document_keeps_its_list(source):
    doc = source.document("infra-002")
    assert doc.restricted_principals == ("group:dba",)
    assert doc.space_principals == ("group:infra", "group:eng")


def test_classification_is_inherited_from_space(source):
    assert source.document("hr-int-001").classification == Classification.CONFIDENTIAL
    assert source.document("hr-001").classification == Classification.GENERAL


def test_empty_space_permission_stays_empty(source):
    assert source.document("misc-001").space_principals == ()


def test_missing_document_is_none(source):
    assert source.document("nope") is None


def test_principals_include_user_and_groups(source):
    assert principals_for("dana", source) == ["user:dana", "group:employees", "group:eng", "group:dba"]
    assert principals_for("user:kim", source) == ["user:kim", "group:partner"]


def test_unknown_user_is_an_error_not_a_user_without_groups(source):
    """그룹 없는 사용자로 보면 WIKI_USER 오타가 "문서가 없다"로 보인다 (M4)."""
    with pytest.raises(UnknownUserError):
        principals_for("stranger", source)


@pytest.mark.parametrize("bad", ["", "user:", "bob smith", "../etc", "..", "bob\n", "a" * 65])
def test_rejects_malformed_user(source, bad):
    with pytest.raises(ValueError):
        principals_for(bad, source)


def test_document_for_resolves_groups_itself(source):
    """본문 재확인은 사용자 id만 받고 그룹과 두 층 권한을 위키 쪽에서 판단한다 (ADR-07, ADR-21)."""
    assert source.document_for("infra-002", "dana").doc_id == "infra-002"
    assert source.document_for("infra-002", "bob") is None  # 스페이스 안이지만 제한 밖
    assert source.document_for("infra-002", "erin") is None  # 제한 대상이지만 스페이스 밖
    assert source.document_for("infra-002", "stranger") is None  # 모르는 사용자도 없는 문서와 같다


class BrokenSource:
    def groups_of(self, user_id):
        raise GroupLookupError("위키 응답 없음")


def test_group_lookup_failure_propagates():
    with pytest.raises(GroupLookupError):
        principals_for("bob", BrokenSource())
