"""관측성 계측(ADR-15). 검색 단계가 도구 스팬 아래에 나뉘어 남는지, 워커가 위키의 추적을 이어받는지 본다.

OpenTelemetry의 전역 제공자는 프로세스에 한 번만 정할 수 있어서, 이 모듈이 메모리에 모으는 제공자를 한 번 정한다.
다른 테스트의 계측도 여기로 모이지만 검사는 이 모듈의 것만 한다.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from mcp import Client
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from tests.fakeencoder import FakeEncoder
from wiki_rag_mcp.auth.groups import GroupCache
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.events import Event, event_fields
from wiki_rag_mcp.indexing.incremental import Applied
from wiki_rag_mcp.indexing.worker import StreamWorker
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.wiki.files import FileWikiSource

FIXTURE = Path(__file__).parent / "fixtures" / "wiki"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


@pytest.fixture(scope="module")
def otel():
    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    if trace.get_tracer_provider() is not provider:
        pytest.skip("다른 곳에서 전역 제공자를 먼저 정했다")
    return spans, reader


def points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    return [p for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name == name
            for p in m.data.data_points] if data else []


class EmptyStore:
    def knn_search(self, _vector, _principals, _k, *, space=None, updated_after=None):
        return []


def test_search_stages_are_children_of_the_tool_span(otel):
    spans, reader = otel
    spans.clear()
    services = Services(Settings(wiki_dir=FIXTURE, user="bob"), FileWikiSource(FIXTURE), EmptyStore(),
                        FakeEncoder())

    async def go():
        async with Client(build_server(services)) as client:
            await client.call_tool("search_wiki", {"query": "휴가 규정"})

    anyio.run(go)
    finished = spans.get_finished_spans()
    tool = next(s for s in finished if s.name == "tools/call search_wiki")
    stages = [s.name for s in finished if s.parent is not None and s.parent.span_id == tool.context.span_id]
    assert stages == ["principals", "embed_query", "vector_search", "assemble"]
    # 쿼리 원문은 어느 스팬에도 남지 않는다
    assert all("휴가" not in str(v) for s in finished for v in (s.attributes or {}).values())
    recorded = {p.attributes["stage"] for p in points(reader, "wiki.search.stage.duration")}
    assert recorded == {"principals", "embed_query", "vector_search", "assemble"}


class FakeRedis:
    """워커가 처리 성공 뒤에 부르는 확인과 큐 길이만 흉내 낸다."""

    def xackdel(self, *_args, **_kwargs):
        return [1]

    def xlen(self, key: str) -> int:
        return 2 if key.endswith(":dlq") else 5


def test_worker_continues_the_trace_of_the_wiki_edit(otel):
    spans, reader = otel
    spans.clear()
    worker = StreamWorker(FakeRedis(), lambda e: Applied(e.doc_id, "indexed", e.revision), [0], prefix="t")
    fields = event_fields(Event("db", 3, "CONTENT_CHANGED", datetime.now(UTC) - timedelta(seconds=2)))

    assert worker.process("t:0", "1-0", {**fields, "traceparent": TRACEPARENT})
    assert worker.process("t:0", "2-0", fields)  # traceparent가 없는 옛 이벤트도 그대로 처리한다

    continued, fresh = spans.get_finished_spans()
    assert continued.name == "process t:0" and continued.kind == SpanKind.CONSUMER
    assert format(continued.context.trace_id, "032x") == TRACE_ID
    assert continued.attributes["wiki.doc_id"] == "db"
    assert fresh.parent is None and format(fresh.context.trace_id, "032x") != TRACE_ID
    lag = [p for p in points(reader, "wiki.indexing.lag") if p.attributes == {"outcome": "indexed"}]
    assert lag and lag[0].count >= 2 and lag[0].min >= 2


class ScriptedRedis(FakeRedis):
    """xreadgroup이 부를 때마다 정해 둔 응답을 차례로 준다."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def xreadgroup(self, *_args, **_kwargs):
        return self.replies.pop(0) if self.replies else {}


def test_membership_drained_before_a_document_is_traced(otel):
    """문서 이벤트 사이에 끼어든 멤버십 이벤트(_drain_membership)도 처리 스팬과 권한 회수 지연을 남긴다."""
    spans, reader = otel
    spans.clear()
    doc = event_fields(Event("db", 3, "CONTENT_CHANGED", datetime.now(UTC)))
    membership = {"user_id": "alice", "type": "MEMBERSHIP_CHANGED", "traceparent": TRACEPARENT,
                  "created_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    redis = ScriptedRedis({"t:0": [("1-0", doc)]}, {"wiki:membership": [("2-0", membership)]})
    invalidated: list[str] = []
    worker = StreamWorker(redis, lambda e: Applied(e.doc_id, "indexed", e.revision), [0], prefix="t",
                          membership=invalidated.append)

    assert worker.poll() == 2
    assert invalidated == ["alice"]
    names = {s.name: s for s in spans.get_finished_spans()}
    assert set(names) == {"process wiki:membership", "process t:0"}
    assert format(names["process wiki:membership"].context.trace_id, "032x") == TRACE_ID
    assert [p for p in points(reader, "wiki.indexing.lag") if p.attributes == {"outcome": "invalidated"}]


def test_queue_lengths_are_observed_per_partition(otel):
    _, reader = otel
    StreamWorker(FakeRedis(), lambda e: None, [0, 1], prefix="q").observe_queues()
    observed = {(p.attributes["queue"], p.attributes.get("partition")): p.value
                for p in points(reader, "wiki.indexing.queue.length")}
    assert {k: v for k, v in observed.items() if k[1] in ("0", "1")} == {
        ("events", "0"): 5, ("dlq", "0"): 2, ("events", "1"): 5, ("dlq", "1"): 2}


class DictRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    def mget(self, *keys):
        return [self.data.get(k) for k in keys]

    def set(self, key, value, ex=None):
        self.data[key] = value


class Wiki:
    def groups_of(self, _user_id):
        return ["group:eng"]


def test_group_cache_counts_hits_and_misses(otel):
    _, reader = otel

    def count(result: str) -> int:
        return sum(p.value for p in points(reader, "wiki.group_cache.lookups") if p.attributes["result"] == result)

    before = count("hit"), count("miss")
    cache = GroupCache(Wiki(), DictRedis())
    cache.groups_of("alice")
    cache.groups_of("alice")
    assert (count("hit"), count("miss")) == (before[0] + 1, before[1] + 1)
