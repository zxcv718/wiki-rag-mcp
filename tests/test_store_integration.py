"""실제 검색 저장소에서 권한 필터의 의미와 결과 수를 확인한다 (4장 검증 방법, ADR-07, ADR-21, ADR-22).

`docker compose up -d postgres`로 저장소를 띄운 뒤 실행한다. 떠 있지 않으면 건너뛴다. M2에서 저장소를 옮길 때
같은 테스트를 OpenSearch와 pgvector에 모두 돌려 권한의 의미가 같은지 확인했다(태그 m2-judgement).
"""

from datetime import UTC, datetime

import numpy as np
import pytest

from tests.pg import new_store

pytestmark = pytest.mark.integration
DIM = 8


@pytest.fixture(scope="module")
def store():
    s = new_store("chunks", DIM)
    yield s
    s.drop()


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return (v / np.linalg.norm(v)).tolist()


def chunk(doc_id, vector, space, restricted, *, space_name="engineering", idx=0):
    return {
        "chunk_id": f"{doc_id}#{idx}", "doc_id": doc_id, "chunk_index": idx, "space": space_name,
        "title": doc_id, "section_path": [], "text": "본문", "url": f"https://wiki.local/{doc_id}",
        "version": 1, "revision": 1, "updated_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
        "section_hash": "h", "space_principals": space, "restricted_principals": restricted,
        "classification": "general", "embedding": vector, "embedding_model": "test",
        "embedding_revision": "test", "embedding_dtype": "float32",
    }


# 사용자별 principal. bob은 개발팀, dana는 개발팀이면서 DBA, erin은 DBA지만 개발팀이 아니다
BOB = ["user:bob", "group:eng"]
DANA = ["user:dana", "group:eng", "group:dba"]
ERIN = ["user:erin", "group:dba"]
Q = unit([1, 0, 0, 0, 0, 0, 0, 0])


@pytest.fixture(scope="module")
def permission_docs(store):
    docs = [
        chunk("eng-open", Q, ["group:eng"], ["all"]),
        chunk("eng-dba-only", Q, ["group:eng"], ["group:dba"]),  # 제한 문서
        chunk("no-space-perm", Q, [], ["all"]),  # 권한 필드가 비어 있음
        chunk("public", Q, ["all"], ["all"]),
    ]
    store.index_chunks(docs)
    return docs


def visible(store, principals):
    return {h["doc_id"] for h in store.knn_search(Q, principals, k=10)}


@pytest.mark.usefixtures("permission_docs")
def test_space_member_without_restriction_cannot_see_restricted_doc(store):
    assert visible(store, BOB) == {"eng-open", "public"}


@pytest.mark.usefixtures("permission_docs")
def test_member_of_both_layers_sees_restricted_doc(store):
    assert visible(store, DANA) == {"eng-open", "eng-dba-only", "public"}


@pytest.mark.usefixtures("permission_docs")
def test_restriction_target_outside_space_cannot_see_restricted_doc(store):
    assert "eng-dba-only" not in visible(store, ERIN)


@pytest.mark.usefixtures("permission_docs")
def test_empty_permission_field_is_visible_to_nobody(store):
    for who in (BOB, DANA, ERIN, []):
        assert "no-space-perm" not in visible(store, who)


@pytest.mark.usefixtures("permission_docs")
def test_anonymous_sees_only_public(store):
    assert visible(store, []) == {"public"}


def test_returns_k_results_even_when_nearest_chunks_are_forbidden():
    """가장 가까운 청크가 모두 권한 밖이어도 볼 수 있는 청크 k개를 돌려받아야 한다.

    사후 필터라면 상위 후보가 전부 걸러져 결과가 0개가 된다(ADR-07이 기각한 문제). pgvector는 필터를 인덱스
    스캔 뒤에 적용하므로, iterative scan이 권한 밖 후보를 지나 더 스캔하는지가 이 테스트로 드러난다.
    """
    s = new_store("count", DIM)
    try:
        rng = np.random.default_rng(0)
        forbidden = [chunk(f"hr-{i}", unit(np.array(Q) + rng.normal(0, 0.01, DIM)), ["group:hr"], ["all"],
                           space_name="hr") for i in range(500)]
        allowed = [chunk(f"eng-{i}", unit(rng.normal(0, 1, DIM)), ["group:eng"], ["all"]) for i in range(50)]
        s.index_chunks(forbidden + allowed)
        hits = s.knn_search(Q, BOB, k=5)
        assert len(hits) == 5
        assert all(h["doc_id"].startswith("eng-") for h in hits)
    finally:
        s.drop()
