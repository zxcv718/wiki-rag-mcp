"""Redis Streams 소비자의 확인·재시도·DLQ·복구를 실제 Redis로 확인한다 (5장 "장애 대응").

`docker compose up -d redis`로 띄운 뒤 실행한다. 떠 있지 않으면 건너뛴다. 이벤트 처리 자체는 가짜로 두고,
스트림을 다루는 규칙만 본다. 처리 결과의 정확성은 test_incremental.py가 본다.
"""

import logging
import uuid
from datetime import UTC, datetime

import pytest
import redis

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.events import Event, event_fields, partition
from wiki_rag_mcp.indexing.incremental import Applied
from wiki_rag_mcp.indexing.worker import StreamWorker

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    c = redis.Redis.from_url(Settings().redis_url, decode_responses=True)
    try:
        c.ping()
    except redis.ConnectionError:
        pytest.skip("로컬 Redis가 떠 있지 않다 (docker compose up -d redis)")
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


def test_partition_matches_the_wiki_service():
    """위키 서비스(Java CRC32)의 테스트와 같은 값이다 (wiki-service/README.md "이벤트")."""
    expected = {"co-001": 1, "eng-022": 1, "infra-011": 3, "hr-007": 0, "fin-011": 0, "data-003": 1}
    assert {d: partition(d, 4) for d in expected} == expected
