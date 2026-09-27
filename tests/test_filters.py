from datetime import UTC, datetime

import pytest

from wiki_rag_mcp.search.filters import ALL, allows, permission_filter, principal_set, search_filter


def test_principal_set_adds_all_and_sorts():
    assert principal_set(["user:bob", "group:eng"]) == [ALL, "group:eng", "user:bob"]


def test_principal_set_without_principals_sees_only_public():
    assert principal_set([]) == [ALL]


@pytest.mark.parametrize("bad", ["bob", "user:", "role:admin", "user:bob;drop", "group:개발"])
def test_principal_set_rejects_malformed(bad):
    with pytest.raises(ValueError):
        principal_set([bad])


def test_permission_filter_requires_both_layers():
    clauses = permission_filter(["group:eng"])
    fields = [next(iter(c["terms"])) for c in clauses]
    assert fields == ["space_principals", "restricted_principals"]
    assert all(c["terms"][f] == [ALL, "group:eng"] for c, f in zip(clauses, fields, strict=True))


def test_search_filter_keeps_optional_conditions_in_the_same_filter():
    since = datetime(2026, 9, 1, tzinfo=UTC)
    f = search_filter(["user:bob"], space="engineering", updated_after=since)
    clauses = f["bool"]["filter"]
    assert {"term": {"space": "engineering"}} in clauses
    assert {"range": {"updated_at": {"gte": since.isoformat()}}} in clauses
    assert len(clauses) == 4


def test_allows_needs_both_layers_and_rejects_empty_fields():
    dba_in_infra = (["group:infra", "group:eng"], ["group:dba"])
    assert allows(["user:dana", "group:eng", "group:dba"], *dba_in_infra)
    assert not allows(["user:erin", "group:dba"], *dba_in_infra)  # 제한 대상이지만 스페이스 밖
    assert not allows(["user:bob", "group:eng"], *dba_in_infra)  # 스페이스 안이지만 제한 밖
    assert allows(["user:kim"], [ALL], [ALL])
    assert not allows(["user:kim"], [], [ALL]) and not allows(["user:kim"], [ALL], [])
