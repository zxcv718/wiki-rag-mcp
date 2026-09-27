import pytest

from wiki_rag_mcp.search.filters import ALL, allows, principal_set, validate_stored_principals


def test_principal_set_adds_all_and_sorts():
    assert principal_set(["user:bob", "group:eng"]) == [ALL, "group:eng", "user:bob"]


def test_principal_set_without_principals_sees_only_public():
    assert principal_set([]) == [ALL]


@pytest.mark.parametrize("bad", ["bob", "user:", "role:admin", "user:bob;drop", "group:개발"])
def test_principal_set_rejects_malformed(bad):
    with pytest.raises(ValueError):
        principal_set([bad])


def test_allows_needs_both_layers_and_rejects_empty_fields():
    dba_in_infra = (["group:infra", "group:eng"], ["group:dba"])
    assert allows(["user:dana", "group:eng", "group:dba"], *dba_in_infra)
    assert not allows(["user:erin", "group:dba"], *dba_in_infra)  # 제한 대상이지만 스페이스 밖
    assert not allows(["user:bob", "group:eng"], *dba_in_infra)  # 스페이스 안이지만 제한 밖
    assert allows(["user:kim"], [ALL], [ALL])
    assert not allows(["user:kim"], [], [ALL]) and not allows(["user:kim"], [ALL], [])


@pytest.mark.parametrize("value", ["group:eng\n", "user:bob\n", "group:eng\nall"])
def test_principal_with_trailing_newline_is_rejected(value):
    """정규식의 $는 끝의 줄바꿈 앞에서도 맞는다. 형식 검사가 이런 값을 통과시키지 않아야 한다."""
    with pytest.raises(ValueError):
        validate_stored_principals([value])
    with pytest.raises(ValueError):
        principal_set([value])


@pytest.mark.parametrize("doc_id", [".", "..", "...", "a/b", "x\n", "", "a" * 65])
def test_doc_ids_that_cannot_be_a_url_path_segment_are_invalid(doc_id):
    from wiki_rag_mcp.models import valid_doc_id

    assert not valid_doc_id(doc_id)
