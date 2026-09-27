"""증분 색인이 이벤트의 중복·순서 뒤바뀜·유실에도 위키와 같은 상태로 끝나는지 확인한다 (ADR-19 검증 방법).

각 시나리오는 위키에서 일어난 변경의 이벤트를 가능한 모든 순서로 재생하고, 최종 인덱스의 권한·등급·삭제
상태와 본문이 위키와 같은지 본다. 바뀐 섹션만 다시 임베딩하는지(ADR-10)도 여기서 센다.
"""

import hashlib
import random
from itertools import permutations

import numpy as np
import pytest

from tests.fakewiki import FakeWiki, Space
from tests.pg import new_store
from wiki_rag_mcp.indexing.chunker import chunk_document
from wiki_rag_mcp.indexing.events import Event
from wiki_rag_mcp.indexing.incremental import StaleRead, apply_event
from wiki_rag_mcp.indexing.reconcile import reconcile
from wiki_rag_mcp.models import Classification
from wiki_rag_mcp.search.pg_store import StoredState
from wiki_rag_mcp.wiki.source import DocState, WikiUnavailableError

pytestmark = pytest.mark.integration
DIM = 8
JIHO = ["user:jiho", "group:employees", "group:eng"]  # 개발팀, DBA 아님
TAEYANG = ["user:taeyang", "group:employees", "group:eng", "group:infra", "group:dba"]

BODY = """# 운영 DB 접근

## 신청

접근은 티켓으로 신청한다. 승인자는 DBA 팀장이다.

## 승인

승인은 하루 안에 난다.

## 회수

권한은 30일 뒤 자동으로 회수된다.
"""


def words(text):
    return len(text.split())


class CountingEncoder:
    model_name, revision, dtype = "fake", "0", "float32"

    def __init__(self):
        self.encoded = 0
        self.fail = False

    def encode_documents(self, texts):
        if self.fail:
            raise RuntimeError("임베딩 실패 (테스트)")
        self.encoded += len(texts)
        vecs = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:DIM], dtype=np.uint8).astype(np.float32) + 1
                for t in texts]
        return np.array([v / np.linalg.norm(v) for v in vecs])


@pytest.fixture(scope="module")
def store():
    s = new_store("incr", DIM)
    yield s
    s.drop()


def reset(store):
    store.conn.execute(f"TRUNCATE {store.table}, {store.table}_doc_state")


@pytest.fixture
def clean(store):
    reset(store)
    return store


def make_wiki():
    return FakeWiki({
        "infra": Space("인프라", ("group:infra", "group:eng")),
        "company": Space("전사 공지", ("all",)),
    })


def apply_all(events, wiki, store, encoder=None):
    encoder = encoder or CountingEncoder()
    return [apply_event(e, wiki, store, encoder, words, sleep=lambda _: None) for e in events]


def rows_of(store, doc_id):
    cur = store.conn.execute(
        f"SELECT text, title, version, revision, space_principals, restricted_principals, classification "
        f"FROM {store.table} WHERE doc_id = %s", [doc_id])
    names = [d.name for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def chunk_ids(store, doc_id):
    cur = store.conn.execute(f"SELECT chunk_id FROM {store.table} WHERE doc_id = %s", [doc_id])
    return [{"chunk_id": r[0]} for r in cur.fetchall()]


def assert_index_matches(wiki, store):
    states = store.doc_states()
    for doc_id in wiki.pages:
        state = wiki.state(doc_id)
        rows = rows_of(store, doc_id)
        if state.deleted:
            assert rows == [], doc_id
            assert states[doc_id] == StoredState(state.revision, True)
            continue
        doc = state.document
        assert states[doc_id] == StoredState(doc.revision, False)
        expected = [c.text for c in chunk_document(doc_id, doc.body, doc.title, words)]
        assert sorted(r["text"] for r in rows) == sorted(expected)
        for r in rows:
            assert set(r["space_principals"]) == set(doc.space_principals)
            assert set(r["restricted_principals"]) == set(doc.restricted_principals)
            assert r["classification"] == doc.classification
            assert (r["title"], r["version"], r["revision"]) == (doc.title, doc.version, doc.revision)


def visible(store, principals):
    q = CountingEncoder().encode_documents(["질문"])[0]
    return {h["doc_id"] for h in store.knn_search(q.tolist(), principals, k=20)}


def replay_every_order(events, wiki, store):
    for order in permutations(events):
        reset(store)
        apply_all(order, wiki, store)
        assert_index_matches(wiki, store)


# ADR-19 검증 시나리오


def test_grant_then_revoke_in_any_order(clean):
    wiki = make_wiki()
    events = [wiki.create("db", "infra", "운영 DB 접근", BODY, restricted=("group:dba",)),
              wiki.restrict("db", ("group:dba", "user:jiho")),  # 부여
              wiki.restrict("db", ("group:dba",))]  # 회수
    replay_every_order(events + events[1:2], wiki, clean)  # 부여 이벤트가 한 번 더 온 경우까지
    assert "db" not in visible(clean, JIHO)
    assert "db" in visible(clean, TAEYANG)


def test_revoke_survives_a_grant_processed_in_between(clean):
    """부여 이벤트를 처리한 뒤 회수가 일어나고, 옛 부여 이벤트가 다시 와도 회수가 유지된다."""
    wiki = make_wiki()
    created = wiki.create("db", "infra", "운영 DB 접근", BODY, restricted=("group:dba",))
    grant = wiki.restrict("db", ("group:dba", "user:jiho"))
    apply_all([created, grant], wiki, clean)
    assert "db" in visible(clean, JIHO)
    revoke = wiki.restrict("db", ("group:dba",))
    outcomes = apply_all([revoke, grant], wiki, clean)
    assert [o.outcome for o in outcomes] == ["indexed", "skipped"]
    assert "db" not in visible(clean, JIHO)
    assert_index_matches(wiki, clean)


def test_revoke_then_edit_in_any_order(clean):
    """회수 직후 본문이 바뀌어 새 이벤트가 먼저 처리돼도 회수가 빠지지 않는다. version으로 순서를 판단하면
    회수 이벤트가 "옛 버전"으로 버려지는 경우다."""
    wiki = make_wiki()
    events = [wiki.create("db", "infra", "운영 DB 접근", BODY),
              wiki.restrict("db", ("group:dba",)),
              wiki.edit("db", body=BODY.replace("하루", "이틀"))]
    replay_every_order(events, wiki, clean)
    assert "db" not in visible(clean, JIHO)


def test_old_events_after_delete_do_not_revive_the_document(clean):
    wiki = make_wiki()
    events = [wiki.create("db", "infra", "운영 DB 접근", BODY),
              wiki.edit("db", body=BODY + "\n## 부록\n\n추가 내용\n"),
              wiki.delete("db")]
    replay_every_order(events, wiki, clean)
    assert "db" not in visible(clean, TAEYANG)


def test_space_viewer_and_default_classification_changes(clean):
    wiki = make_wiki()
    creates = [wiki.create(f"infra-{i}", "infra", f"문서 {i}", BODY) for i in range(3)]
    bumps = wiki.set_viewers("infra", ("group:infra",)) + wiki.set_space_classification(
        "infra", Classification.CONFIDENTIAL)
    rng = random.Random(20260927)
    for _ in range(20):
        order = creates + bumps
        rng.shuffle(order)
        reset(clean)
        apply_all(order, wiki, clean)
        assert_index_matches(wiki, clean)
    assert visible(clean, JIHO) == set()  # 개발팀은 스페이스 권한을 잃었다


def test_failure_mid_write_is_retried_not_skipped(clean, monkeypatch):
    """청크를 쓰다 실패하면 상태 기록까지 함께 되돌아가, 재시도가 "이미 반영됨"으로 건너뛰지 않는다."""
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY)], wiki, clean)
    edit = wiki.edit("db", body=BODY.replace("하루", "이틀"))

    def broken(*_args, **_kwargs):
        raise RuntimeError("상태 기록 직전 실패 (테스트)")

    monkeypatch.setattr(clean, "_write_state", broken)
    with pytest.raises(RuntimeError):
        apply_all([edit], wiki, clean)
    assert clean.doc_state("db").revision == 1
    assert all("이틀" not in r["text"] for r in rows_of(clean, "db"))  # 청크 교체도 되돌아갔다

    monkeypatch.undo()
    assert apply_all([edit], wiki, clean)[0].outcome == "indexed"
    assert_index_matches(wiki, clean)


def test_revocation_applies_while_embedding_fails(clean):
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY)], wiki, clean)
    assert "db" in visible(clean, JIHO)
    revoke = wiki.restrict("db", ("group:dba",))
    edit = wiki.edit("db", body=BODY.replace("하루", "이틀"))  # 새 본문이라 임베딩이 필요하다
    failing = CountingEncoder()
    failing.fail = True
    for event in (revoke, edit):
        with pytest.raises(RuntimeError):
            apply_all([event], wiki, clean, failing)
    assert "db" not in visible(clean, JIHO)  # 본문은 못 바꿨지만 권한 회수는 적용됐다
    assert clean.doc_state("db").revision == 1  # 상태는 그대로라 재시도가 다시 처리한다
    apply_all([revoke, edit], wiki, clean)
    assert_index_matches(wiki, clean)


def test_lost_revocation_is_repaired_by_reconcile(clean):
    """회수 이벤트가 DLQ에서 끝내 처리되지 못해도 야간 정합성 배치가 revision 차이로 찾아 다시 넣는다."""
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY)], wiki, clean)
    wiki.restrict("db", ("group:dba",))  # 이 이벤트는 유실됐다
    assert "db" in visible(clean, JIHO)

    published = []
    report = reconcile(wiki, clean, published.append)
    assert published == [Event("db", 2, "RECONCILE")]
    assert report.anomalies == []
    apply_all(published, wiki, clean)
    assert "db" not in visible(clean, JIHO)
    assert reconcile(wiki, clean, published.append).republished == []  # 맞춰진 뒤에는 넣을 것이 없다


def test_reconcile_removes_documents_missing_from_the_wiki(clean):
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY), wiki.create("keep", "company", "공지", BODY)],
              wiki, clean)
    del wiki.pages["db"]  # 위키에서 행이 사라졌다 (예: 관리자가 DB에서 직접 지움)
    published = []
    reconcile(wiki, clean, published.append)
    assert published == [Event("db", 2, "RECONCILE")]
    apply_all(published, wiki, clean)
    assert rows_of(clean, "db") == []
    assert clean.doc_state("db") == StoredState(2, True)
    assert rows_of(clean, "keep")


def test_reconcile_picks_up_chunks_indexed_before_doc_state_existed(clean):
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY)], wiki, clean)
    clean.conn.execute(f"TRUNCATE {clean.table}_doc_state")
    published = []
    reconcile(wiki, clean, published.append)
    assert published == [Event("db", 1, "RECONCILE")]


def test_duplicate_event_is_skipped_without_reading_the_wiki(clean):
    wiki = make_wiki()
    created = wiki.create("db", "infra", "운영 DB 접근", BODY)
    apply_all([created], wiki, clean)
    reads = wiki.reads
    assert apply_all([created], wiki, clean)[0].outcome == "skipped"
    assert wiki.reads == reads


def test_stale_read_is_retried_then_raised(clean):
    wiki = make_wiki()
    created = wiki.create("db", "infra", "운영 DB 접근", BODY)
    ahead = Event("db", 5, "CONTENT_CHANGED")  # 위키가 아직 커밋하지 않은 revision
    with pytest.raises(StaleRead):
        apply_all([ahead], wiki, clean)
    assert wiki.reads == 3
    assert clean.doc_state("db") is None
    apply_all([created], wiki, clean)
    assert_index_matches(wiki, clean)


def test_wiki_outage_fails_without_touching_the_index(clean):
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY)], wiki, clean)
    revoke = wiki.restrict("db", ("group:dba",))
    wiki.unavailable = True
    with pytest.raises(WikiUnavailableError):
        apply_all([revoke], wiki, clean)
    assert clean.doc_state("db").revision == 1


def test_never_existing_document_is_recorded_as_deleted(clean):
    class Empty(FakeWiki):
        def state(self, doc_id):
            return DocState(doc_id, None, None)

    outcome = apply_all([Event("ghost", 3, "RECONCILE")], Empty({}), clean)[0]
    assert (outcome.outcome, outcome.revision) == ("deleted", 3)
    assert clean.doc_state("ghost") == StoredState(3, True)


# ADR-10: 바뀐 섹션만 다시 임베딩


def test_only_changed_sections_are_embedded(clean):
    wiki = make_wiki()
    encoder = CountingEncoder()

    def step(event):
        before = encoder.encoded
        applied = apply_event(event, wiki, clean, encoder, words)
        return encoder.encoded - before, applied

    embedded, applied = step(wiki.create("db", "infra", "운영 DB 접근", BODY))
    assert (embedded, applied.embedded, applied.reused) == (3, 3, 0)

    fixed = BODY.replace("하루", "이틀")
    embedded, applied = step(wiki.edit("db", body=fixed))  # 한 섹션의 오타 수정
    assert (embedded, applied.reused) == (1, 2)

    embedded, _ = step(wiki.restrict("db", ("group:dba",)))  # 권한 변경은 임베딩하지 않는다
    assert embedded == 0

    embedded, _ = step(wiki.classify("db", Classification.CONFIDENTIAL))
    assert embedded == 0

    extended = fixed + "\n## 문의\n\n#dba 채널로 묻는다.\n"
    embedded, applied = step(wiki.edit("db", body=extended))  # 섹션 추가
    assert (embedded, applied.reused) == (1, 3)

    # 제목을 바꾸면서 본문 첫 줄의 제목도 같이 바꾼다. 청크 id(섹션 해시)는 그대로지만, 제목은 모든 청크의
    # 맥락 헤더에 들어가므로 전부 다시 임베딩해야 한다
    extended = extended.replace("# 운영 DB 접근\n", "# 운영 DB 접근 절차\n")
    before_ids = {r["chunk_id"] for r in chunk_ids(clean, "db")}
    embedded, _ = step(wiki.edit("db", title="운영 DB 접근 절차", body=extended))
    assert {r["chunk_id"] for r in chunk_ids(clean, "db")} == before_ids
    assert embedded == 4

    embedded, _ = step(wiki.edit("db", body=extended.split("## 회수")[0]))  # 뒤의 두 섹션 삭제
    assert embedded == 0
    assert len(rows_of(clean, "db")) == 2
    assert_index_matches(wiki, clean)


def test_store_refuses_to_move_a_document_back_to_an_older_revision(clean):
    """문서별 직렬 처리가 깨져 옛 처리가 늦게 끝나도 인덱스가 되돌아가지 않는다.

    파티션 수를 바꾸다 옛 워커가 남아 같은 문서를 두 워커가 처리하는 경우다."""
    wiki = make_wiki()
    apply_all([wiki.create("db", "infra", "운영 DB 접근", BODY), wiki.edit("db", body=BODY + "\n## 부록\n\n내용\n")],
              wiki, clean)
    rows = rows_of(clean, "db")
    assert clean.replace_document("db", [], revision=1) is False
    assert clean.mark_deleted("db", revision=1) is False
    assert rows_of(clean, "db") == rows
    assert clean.doc_state("db") == StoredState(2, False)
