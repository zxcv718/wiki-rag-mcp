"""Redis Streams 소비자의 확인·재시도·DLQ·복구를 실제 Redis로 확인한다 (5장 "장애 대응").

`docker compose up -d redis`로 띄운 뒤 실행한다. 떠 있지 않으면 건너뛴다. 이벤트 처리 자체는 가짜로 두고,
스트림을 다루는 규칙만 본다. 처리 결과의 정확성은 test_incremental.py가 본다.
"""

import logging
import uuid
from datetime import UTC, datetime

import pytest
import redis

from tests.services import redis_client
from wiki_rag_mcp.indexing.events import Event, event_fields, partition
from wiki_rag_mcp.indexing.incremental import Applied
from wiki_rag_mcp.indexing.worker import StreamWorker

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    c = redis_client()
    yield c
    c.close()


@pytest.fixture
def prefix(client):
    p = f"test:{uuid.uuid4().hex[:8]}:events"
    yield p
    for key in client.scan_iter(f"{p}*"):
        client.delete(key)


class Handler:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.seen: list[Event] = []

    def __call__(self, event: Event) -> Applied:
        self.seen.append(event)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("처리 실패 (테스트)")
        return Applied(event.doc_id, "indexed", event.revision)


def make(client, prefix, handler, **kwargs):
    sleeps: list[float] = []
    worker = StreamWorker(client, handler, [0], prefix=prefix, sleep=sleeps.append, **kwargs)
    worker.ensure_groups()
    return worker, sleeps


def publish(client, prefix, doc_id="db", revision=1, **extra):
    fields = event_fields(Event(doc_id, revision, "CONTENT_CHANGED", datetime.now(UTC)))
    return client.xadd(f"{prefix}:0", {**fields, **extra})


def pending(client, prefix):
    return client.xpending(f"{prefix}:0", "indexer")["pending"]


def test_processed_events_are_acked_and_removed(client, prefix):
    handler = Handler()
    worker, _ = make(client, prefix, handler)
    publish(client, prefix, revision=1)
    publish(client, prefix, revision=2)
    assert worker.poll(block_ms=100) == 2
    assert [e.revision for e in handler.seen] == [1, 2]  # 같은 스트림은 들어온 순서대로
    assert client.xlen(f"{prefix}:0") == 0  # 처리한 이벤트는 스트림에서 지워진다
    assert pending(client, prefix) == 0


def test_events_published_before_the_group_exists_are_read(client, prefix):
    publish(client, prefix)  # 워커가 뜨기 전에 위키가 발행했다
    handler = Handler()
    worker, _ = make(client, prefix, handler)
    assert worker.poll(block_ms=100) == 1


def test_transient_failure_is_retried_with_backoff(client, prefix):
    handler = Handler(failures=2)
    worker, sleeps = make(client, prefix, handler)
    publish(client, prefix)
    worker.poll(block_ms=100)
    assert len(handler.seen) == 3
    assert sleeps == [0.5, 1.0]
    assert client.xlen(f"{prefix}:0") == 0
    assert client.xlen(f"{prefix}:0:dlq") == 0


def test_repeated_failure_moves_the_event_to_the_dlq(client, prefix):
    handler = Handler(failures=100)
    worker, sleeps = make(client, prefix, handler)
    publish(client, prefix, doc_id="db", revision=7)
    worker.poll(block_ms=100)
    assert len(handler.seen) == 4  # 처음 한 번 + 재시도 3회
    assert sleeps == [0.5, 1.0, 2.0]
    assert client.xlen(f"{prefix}:0") == 0
    assert pending(client, prefix) == 0
    [(_, dead)] = client.xrange(f"{prefix}:0:dlq")
    assert (dead["doc_id"], dead["revision"], dead["dlq_count"]) == ("db", "7", "1")
    assert "처리 실패" in dead["error"]


def test_malformed_event_goes_to_the_dlq_without_retries(client, prefix):
    handler = Handler()
    worker, sleeps = make(client, prefix, handler)
    publish(client, prefix, doc_id="../admin")
    worker.poll(block_ms=100)
    assert handler.seen == [] and sleeps == []
    assert client.xlen(f"{prefix}:0:dlq") == 1


def test_dlq_is_requeued_and_repeated_failures_raise_an_alert(client, prefix, caplog):
    handler = Handler(failures=100)
    worker, _ = make(client, prefix, handler)
    publish(client, prefix)
    for _ in range(3):
        worker.poll(block_ms=100)  # 실패해서 DLQ로 간다
        assert worker.requeue_dlq() == 1  # 원래 스트림으로 돌아온다
    assert "ALERT" in caplog.text and "3번" in caplog.text

    handler.failures = 0  # 원인이 해결됐다
    worker.poll(block_ms=100)
    assert client.xlen(f"{prefix}:0") == 0
    assert client.xlen(f"{prefix}:0:dlq") == 0


def test_events_left_by_a_crashed_consumer_are_recovered(client, prefix):
    publish(client, prefix, revision=1)
    publish(client, prefix, revision=2)
    StreamWorker(client, Handler(), [0], prefix=prefix).ensure_groups()
    # 이전 소비자가 읽기만 하고 확인하기 전에 죽었다
    client.xreadgroup("indexer", "old-consumer", {f"{prefix}:0": ">"}, count=10)
    assert pending(client, prefix) == 2

    handler = Handler()
    worker, _ = make(client, prefix, handler, consumer="new-consumer")
    assert worker.recover_pending() == 2
    assert [e.revision for e in handler.seen] == [1, 2]
    assert pending(client, prefix) == 0


def test_run_stops_and_logs_latency(client, prefix, caplog):
    import threading

    caplog.set_level(logging.INFO, logger="wiki_rag_mcp.indexing.worker")
    stop = threading.Event()

    def handle(event):
        stop.set()
        return Applied(event.doc_id, "indexed", event.revision, embedded=1)

    worker, _ = make(client, prefix, handle)
    publish(client, prefix)
    worker.run(stop, block_ms=100)
    assert "latency_ms=" in caplog.text


def membership_worker(client, prefix, handler, invalidated, **kwargs):
    worker = StreamWorker(client, handler, [0], prefix=prefix, membership=invalidated.append,
                          membership_stream=f"{prefix}:membership", sleep=lambda _: None, **kwargs)
    worker.ensure_groups()
    return worker


def test_membership_events_invalidate_before_document_events(client, prefix):
    """임베딩할 문서 이벤트가 몰려도 그룹에서 빠진 사람의 권한이 늦게 줄지 않게 멤버십을 먼저 처리한다 (ADR-08)."""
    order: list[str] = []
    invalidated: list[str] = []

    def handler(event):
        order.append(f"doc:{event.doc_id}")
        return Applied(event.doc_id, "indexed", event.revision)

    worker = membership_worker(client, prefix, handler, invalidated)
    publish(client, prefix, doc_id="db")
    client.xadd(f"{prefix}:membership", {"user_id": "jiho", "type": "MEMBERSHIP_CHANGED", "outbox_id": "9",
                                         "created_at": "2026-09-28T00:00:00Z"})
    worker.membership = lambda user_id: (invalidated.append(user_id), order.append(f"member:{user_id}"))
    assert worker.poll(block_ms=100) == 2
    assert order == ["member:jiho", "doc:db"]
    assert client.xlen(f"{prefix}:membership") == 0  # 확인과 함께 지워 스트림이 쌓이지 않는다


@pytest.mark.parametrize("fields", [{"user_id": "../x", "type": "MEMBERSHIP_CHANGED"},
                                    {"user_id": "jiho", "type": "SOMETHING_ELSE"}, {"type": "MEMBERSHIP_CHANGED"}])
def test_malformed_membership_event_is_dropped(client, prefix, fields, caplog):
    invalidated: list[str] = []
    worker = membership_worker(client, prefix, Handler(), invalidated)
    client.xadd(f"{prefix}:membership", fields)
    worker.poll(block_ms=100)
    assert invalidated == [] and client.xlen(f"{prefix}:membership") == 0
    assert "잘못된 멤버십 이벤트" in caplog.text


class DropsOnce:
    """처음 한 번의 확인(XACKDEL)에서 연결이 끊기거나 응답이 없는 Redis. 나머지는 진짜 Redis로 보낸다."""

    def __init__(self, client, error=redis.ConnectionError):
        self.client = client
        self.error = error
        self.dropped = False

    def xackdel(self, *args, **kwargs):
        if not self.dropped:
            self.dropped = True
            raise self.error("연결 끊김 (테스트)")
        return self.client.xackdel(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


@pytest.mark.parametrize("error", [redis.ConnectionError, redis.TimeoutError])
def test_events_read_before_a_connection_drop_are_processed_after_reconnect(client, prefix, error):
    """한 번에 읽은 이벤트 중 뒤쪽은 연결이 끊기면 처리되지 못한 채 남는다. 새 이벤트만 읽어서는 다시 오지 않으므로
    연결이 돌아오면 남은 이벤트를 다시 가져와 처리한다. 응답 없는 Redis를 소켓 타임아웃으로 끊는 경우(TimeoutError,
    ConnectionError의 하위 클래스가 아님)도 같다."""
    import threading

    for revision in (1, 2, 3):
        publish(client, prefix, doc_id=f"doc-{revision}", revision=revision)
    stop = threading.Event()
    done: set[str] = set()

    def handler(event):
        done.add(event.doc_id)
        if done == {"doc-1", "doc-2", "doc-3"}:
            stop.set()
        return Applied(event.doc_id, "indexed", event.revision)

    worker = StreamWorker(DropsOnce(client, error), handler, [0], prefix=prefix, sleep=lambda _: None)
    guard = threading.Timer(5.0, stop.set)  # 복구하지 못하면 영영 끝나지 않으므로 5초 뒤 멈추고 실패로 본다
    guard.start()
    worker.run(stop, block_ms=100)
    guard.cancel()
    assert done == {"doc-1", "doc-2", "doc-3"}
    assert pending(client, prefix) == 0


def test_membership_arriving_mid_batch_goes_before_the_next_document(client, prefix):
    """문서 묶음을 처리하는 도중에 들어온 멤버십 이벤트도 남은 문서 이벤트보다 먼저 처리한다."""
    order: list[str] = []

    def handler(event):
        order.append(f"doc:{event.doc_id}")
        if event.doc_id == "doc-1":  # 첫 문서를 임베딩하는 사이에 누군가 그룹에서 빠졌다
            client.xadd(f"{prefix}:membership", {"user_id": "jiho", "type": "MEMBERSHIP_CHANGED"})
        return Applied(event.doc_id, "indexed", event.revision)

    worker = membership_worker(client, prefix, handler, [])
    worker.membership = lambda user_id: order.append(f"member:{user_id}")
    for n in (1, 2, 3):
        publish(client, prefix, doc_id=f"doc-{n}", revision=n)
    worker.poll(block_ms=100)
    assert order == ["doc:doc-1", "member:jiho", "doc:doc-2", "doc:doc-3"]


def test_group_removed_while_running_is_recreated(client, prefix):
    """Redis가 데이터 없이 다시 뜨면 소비자 그룹이 사라진다. 워커는 죽지 않고 그룹을 다시 만들어 이어서 읽는다."""
    import threading

    stop = threading.Event()
    seen: list[str] = []

    def handler(event):
        seen.append(event.doc_id)
        if event.doc_id == "first":
            client.xgroup_destroy(f"{prefix}:0", "indexer")
            publish(client, prefix, doc_id="second")
        else:
            stop.set()
        return Applied(event.doc_id, "indexed", event.revision)

    worker = StreamWorker(client, handler, [0], prefix=prefix, sleep=lambda _: None)
    publish(client, prefix, doc_id="first")
    guard = threading.Timer(5.0, stop.set)
    guard.start()
    worker.run(stop, block_ms=100)
    guard.cancel()
    assert seen[-1] == "second"


def test_partition_matches_the_wiki_service():
    """위키 서비스(Java CRC32)의 테스트와 같은 값이다 (wiki-service/README.md "이벤트")."""
    expected = {"co-001": 1, "eng-022": 1, "infra-011": 3, "hr-007": 0, "fin-011": 0, "data-003": 1}
    assert {d: partition(d, 4) for d in expected} == expected
