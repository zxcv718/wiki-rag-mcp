"""인덱싱 이벤트를 Redis Streams에서 받아 처리하는 소비자 (ADR-09, ADR-19, 5장 "장애 대응").

- 스트림(파티션)마다 소비자를 하나만 둔다. 같은 문서의 이벤트는 늘 같은 스트림으로 오므로 한 번에 하나씩
  처리된다. 워커를 늘릴 때는 파티션을 나눠 맡기고(--partitions), 파티션 수를 바꿀 때는 기존 워커를 먼저 멈춘다.
- 처리에 성공하면 XACKDEL ... ACKED로 확인과 삭제를 한 번에 한다. 스트림에는 처리 안 된 이벤트만 남는다.
- 실패하면 지수 백오프로 최대 3회 재시도하고, 그래도 실패하면 DLQ 스트림으로 옮긴다. 재시도하는 동안 같은
  스트림의 뒤 이벤트는 기다린다. 무한 재시도가 뒤 이벤트를 영영 막지 않게 하려고 횟수를 제한한다.
- DLQ는 10분마다 원래 스트림에 다시 넣는다. 처리가 멱등이라 여러 번 들어가도 결과가 같고, 권한 회수가 야간
  배치까지 밀리지 않는다. 같은 이벤트가 계속 실패하면 알림 로그를 남긴다.
- 시작할 때와 Redis 연결이 끊겼다 돌아왔을 때, 읽고 확인하지 못한 채 남은 이벤트를 가져와 먼저 처리한다.
  연결이 끊기면 한 번에 읽은 이벤트 중 뒤쪽은 처리하지 못한 채 남는데, 새 이벤트만 읽어서는 다시 오지 않는다.
- 멤버십 이벤트(`wiki:membership`)도 같은 호출로 읽어 그룹 캐시를 무효화한다(ADR-08). 한 번에 읽은 묶음에서
  먼저 처리하고, 문서 이벤트를 하나 처리하기 전마다 멤버십 스트림을 기다리지 않고 한 번 더 읽는다. 임베딩이
  몰려도 그룹에서 빠진 사람의 검색 권한이 문서 묶음을 다 처리할 때까지 밀리지 않게 하기 위해서다. 한 문서
  이벤트의 재시도(최대 3.5초) 동안은 기다린다. 무효화는 여러 번 해도 결과가 같아서, 워커가 여럿이면 같은
  소비자 그룹으로 나눠 받는다. 워커가 멈춰도 캐시 TTL(60초)이 지나면 반영된다.
- Redis가 응답 없이 멈추면(연결이 반만 열린 채 남는 경우) 소켓 타임아웃으로 끊고 연결 끊김과 같이 복구한다.
  클라이언트는 cli.py가 타임아웃과 keepalive를 넣어 만든다.
- 이벤트 하나마다 처리 스팬을 연다. 위키가 이벤트에 실어 보낸 traceparent(문서를 고친 요청의 추적)를 부모로
  이어서, 편집부터 검색 반영까지가 한 추적에 보인다(ADR-15). OpenTelemetry 메시징 규약은 기본으로 링크를
  권하지만, 이 워커처럼 메시지 하나를 다른 스팬 밖에서 처리할 때는 만든 쪽의 추적을 부모로 써도 된다고 둔다.
"""

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import redis
from opentelemetry import metrics, propagate, trace
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.trace import SpanKind, StatusCode

from wiki_rag_mcp.indexing.events import (
    GROUP,
    MEMBERSHIP_STREAM,
    STREAM_PREFIX,
    Event,
    dlq_name,
    parse_event,
    parse_membership,
    stream_name,
)
from wiki_rag_mcp.indexing.incremental import Applied

log = logging.getLogger("wiki_rag_mcp.indexing.worker")

RETRIES = 3
BACKOFF_SECONDS = 0.5
REQUEUE_EVERY_SECONDS = 600.0
ALERT_AFTER = 3  # DLQ에 이만큼 들어간 이벤트는 사람이 봐야 한다
_DLQ_ONLY_FIELDS = ("error", "failed_at")

_tracer = trace.get_tracer(__name__)
_meter = metrics.get_meter(__name__)
# 인덱싱 지연(ADR-15, 5장 "측정 지표"): 아웃박스에 기록된 때부터 반영을 마칠 때까지. 목표는 p95 30초 이하다.
# 멤버십 이벤트는 그룹 캐시를 무효화하기까지를 잰다. 그룹에서 빠진 사람의 검색 권한이 회수되기까지다(ADR-08)
_lag_seconds = _meter.create_histogram(
    "wiki.indexing.lag", unit="s",
    description="위키가 이벤트를 기록한 때부터 반영하기까지. outcome: indexed, deleted, skipped(이미 반영됨), "
                "invalidated(그룹 캐시 무효화)",
    explicit_bucket_boundaries_advisory=[0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 60.0, 120.0,
                                         300.0])


def _record_lag(created_at: datetime, outcome: str) -> float:
    seconds = (datetime.now(UTC) - created_at).total_seconds()
    _lag_seconds.record(seconds, {"outcome": outcome})
    return seconds


class StreamWorker:
    def __init__(self, client: redis.Redis, handle: Callable[[Event], Applied], partitions: Iterable[int], *,
                 membership: Callable[[str], None] | None = None, membership_stream: str = MEMBERSHIP_STREAM,
                 prefix: str = STREAM_PREFIX, group: str = GROUP, consumer: str | None = None,
                 start_id: str = "0", retries: int = RETRIES, backoff: float = BACKOFF_SECONDS,
                 requeue_every: float = REQUEUE_EVERY_SECONDS, alert_after: int = ALERT_AFTER,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        parts = sorted(set(partitions))
        self.client = client
        self.handle = handle
        self.membership = membership
        self.membership_stream = membership_stream if membership else None
        self.streams = {stream_name(p, prefix): dlq_name(p, prefix) for p in parts}
        self.group = group
        # 소비자 그룹을 만들 때(또는 Redis가 데이터 없이 다시 떠 다시 만들 때) 읽기 시작할 위치
        self.start_id = start_id
        # 재시작해도 같은 이름을 쓰게 파티션으로 이름을 만든다
        self.consumer = consumer or "indexer-" + "-".join(map(str, parts))
        self.retries, self.backoff = retries, backoff
        self.requeue_every, self.alert_after = requeue_every, alert_after
        self.sleep, self.clock = sleep, clock
        self._next_requeue = clock() + requeue_every

    def _all_streams(self) -> list[str]:
        # 멤버십을 앞에 둔다. 한 번에 읽은 결과도 이 순서로 처리한다
        return [s for s in (self.membership_stream, *self.streams) if s]

    def ensure_groups(self) -> None:
        # 기본은 id 0으로 만들어, 워커가 처음 뜨기 전에 발행된 이벤트도 읽는다
        for stream in self._all_streams():
            try:
                self.client.xgroup_create(stream, self.group, id=self.start_id, mkstream=True)
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

    def recover_pending(self) -> int:
        """확인되지 않은 채 남은 이벤트를 이 소비자로 가져와 처리한다.

        문서 스트림은 소비자가 하나뿐이라, 남아 있는 것은 모두 처리하다 멈춘 이벤트다. 멤버십 스트림은 워커끼리
        나눠 받아서 다른 워커가 처리 중인 이벤트를 가져올 수도 있는데, 무효화를 한 번 더 할 뿐이다.
        """
        handled = 0
        for stream in self._all_streams():
            start = "0-0"
            while True:
                start, claimed, *_ = self.client.xautoclaim(stream, self.group, self.consumer, min_idle_time=0,
                                                            start_id=start, count=100)
                for entry_id, fields in claimed:
                    self.process(stream, entry_id, fields)
                    handled += 1
                if start == "0-0":
                    break
        return handled

    def poll(self, block_ms: int = 1000) -> int:
        replies = self.client.xreadgroup(self.group, self.consumer, dict.fromkeys(self._all_streams(), ">"),
                                         count=10, block=block_ms)
        items = dict(replies.items() if isinstance(replies, dict) else (replies or []))
        handled = 0
        for stream in self._all_streams():
            for entry_id, fields in items.get(stream, []):
                if stream != self.membership_stream:
                    handled += self._drain_membership()
                self.process(stream, entry_id, fields)
                handled += 1
        return handled

    def _drain_membership(self) -> int:
        """그사이 들어온 멤버십 이벤트를 기다리지 않고 읽어 처리한다."""
        if self.membership_stream is None:
            return 0
        replies = self.client.xreadgroup(self.group, self.consumer, {self.membership_stream: ">"}, count=100)
        items = dict(replies.items() if isinstance(replies, dict) else (replies or []))
        entries = items.get(self.membership_stream, [])
        for entry_id, fields in entries:
            self.process(self.membership_stream, entry_id, fields)
        return len(entries)

    def process(self, stream: str, entry_id: str, fields: dict[str, str]) -> bool:
        # traceparent가 없거나 깨져 있으면 빈 맥락이 나와 새 추적이 된다. 처리는 그대로 한다
        attributes = {"messaging.system": "redis", "messaging.operation.type": "process",
                      "messaging.destination.name": stream, "messaging.message.id": entry_id}
        if "doc_id" in fields:
            attributes["wiki.doc_id"] = fields["doc_id"]
        with _tracer.start_as_current_span(f"process {stream}", context=propagate.extract(fields),
                                           kind=SpanKind.CONSUMER, attributes=attributes) as span:
            ok = self._process(stream, entry_id, fields)
            if not ok:
                span.set_status(StatusCode.ERROR)
            return ok

    def _process(self, stream: str, entry_id: str, fields: dict[str, str]) -> bool:
        if stream == self.membership_stream:
            return self._process_membership(entry_id, fields)
        try:
            event = parse_event(fields)
        except (KeyError, ValueError) as e:
            self._to_dlq(stream, entry_id, fields, f"잘못된 이벤트: {e}")
            return False
        error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                applied = self.handle(event)
            except Exception as e:  # 원인과 관계없이 재시도하고, 끝내 실패하면 DLQ에서 본다
                error = e
                log.warning("처리 실패 doc_id=%s revision=%d attempt=%d error=%r", event.doc_id, event.revision,
                            attempt + 1, e)
                if attempt < self.retries:
                    self.sleep(self.backoff * 2 ** attempt)
                continue
            self.client.xackdel(stream, self.group, entry_id, ref_policy="ACKED")
            self._log_applied(event, applied)
            return True
        self._to_dlq(stream, entry_id, fields, repr(error))
        return False

    def _process_membership(self, entry_id: str, fields: dict[str, str]) -> bool:
        """그룹 캐시를 무효화한다. Redis 오류는 그대로 올려, 연결이 돌아온 뒤 복구에서 다시 처리하게 한다.

        형식이 틀린 이벤트는 다시 넣어도 고칠 수 없어 기록만 하고 지운다. 그 사용자의 캐시는 TTL로 바뀐다.
        """
        assert self.membership is not None and self.membership_stream is not None
        try:
            user_id = parse_membership(fields)
        except ValueError as e:
            log.error("잘못된 멤버십 이벤트를 버린다: %s fields=%s", e, fields)
            self.client.xackdel(self.membership_stream, self.group, entry_id, ref_policy="ACKED")
            return False
        self.membership(user_id)
        self.client.xackdel(self.membership_stream, self.group, entry_id, ref_policy="ACKED")
        # 시각이 없거나 깨져 있거나 시간대가 없어도 무효화는 끝났다. 지연만 남기지 않는다
        with contextlib.suppress(ValueError, TypeError):
            _record_lag(datetime.fromisoformat(fields.get("created_at", "")), "invalidated")
        log.info("그룹 캐시 무효화 user_id=%s", user_id)
        return True

    def _to_dlq(self, stream: str, entry_id: str, fields: dict[str, str], message: str) -> None:
        count = int(fields.get("dlq_count", "0")) + 1
        record = {k: v for k, v in fields.items() if k not in _DLQ_ONLY_FIELDS}
        record.update(dlq_count=str(count), error=message[:500], failed_at=datetime.now(UTC).isoformat())
        # DLQ에 넣기와 원래 스트림에서 빼기를 한 번에 한다. 사이에 죽어도 이벤트가 사라지거나 둘로 늘지 않는다
        pipe = self.client.pipeline(transaction=True)
        pipe.xadd(self.streams[stream], record)
        pipe.xackdel(stream, self.group, entry_id, ref_policy="ACKED")
        pipe.execute()
        log.error("DLQ로 옮김 stream=%s doc_id=%s revision=%s dlq_count=%d error=%s", stream, fields.get("doc_id"),
                  fields.get("revision"), count, message[:200])

    def requeue_dlq(self) -> int:
        moved = 0
        for stream, dlq in self.streams.items():
            for entry_id, fields in self.client.xrange(dlq, count=1000):
                count = int(fields.get("dlq_count", "1"))
                if count >= self.alert_after:
                    log.error("ALERT 같은 이벤트가 DLQ에 %d번 들어갔다 doc_id=%s revision=%s error=%s", count,
                              fields.get("doc_id"), fields.get("revision"), fields.get("error"))
                pipe = self.client.pipeline(transaction=True)
                pipe.xadd(stream, {k: v for k, v in fields.items() if k not in _DLQ_ONLY_FIELDS})
                pipe.xdel(dlq, entry_id)
                pipe.execute()
                moved += 1
        return moved

    def observe_queues(self) -> None:
        """스트림에 남은(처리 안 된) 이벤트와 DLQ에 쌓인 이벤트 수를 지표로 낸다(ADR-15). 워커 시작 때 한 번 부른다.

        처리한 이벤트는 XACKDEL로 지우므로 스트림 길이가 곧 밀린 이벤트 수다. 값은 지표를 보낼 때 읽는다.
        """
        def observe(_options: CallbackOptions):
            try:
                if self.membership_stream:
                    yield Observation(self.client.xlen(self.membership_stream), {"queue": "membership"})
                for stream, dlq in self.streams.items():
                    part = stream.rsplit(":", 1)[1]
                    yield Observation(self.client.xlen(stream), {"queue": "events", "partition": part})
                    yield Observation(self.client.xlen(dlq), {"queue": "dlq", "partition": part})
            except redis.RedisError as e:
                log.warning("큐 길이를 읽지 못했다: %r", e)

        _meter.create_observable_gauge("wiki.indexing.queue.length", [observe], unit="{event}",
                                       description="처리 안 된 이벤트 수. queue: events(문서), dlq, membership")

    def _log_applied(self, event: Event, applied: Applied) -> None:
        latency: Any = "-"
        if event.created_at is not None:
            latency = int(_record_lag(event.created_at, applied.outcome) * 1000)
        log.info("%s doc_id=%s revision=%d embedded=%d reused=%d latency_ms=%s", applied.outcome, event.doc_id,
                 applied.revision, applied.embedded, applied.reused, latency)

    def run(self, stop: threading.Event | None = None, block_ms: int = 1000) -> None:
        stop = stop or threading.Event()
        recover = True
        while not stop.is_set():
            try:
                if recover:
                    self.ensure_groups()
                    self.recover_pending()
                    recover = False
                if self.clock() >= self._next_requeue:
                    self.requeue_dlq()
                    self._next_requeue = self.clock() + self.requeue_every
                self.poll(block_ms)
            except redis.ResponseError as e:
                if "NOGROUP" not in str(e):
                    raise
                # Redis가 데이터 없이 다시 떴다. 그룹은 다음 바퀴에서 다시 만들고, 잃은 이벤트는 야간 정합성 배치가
                # 찾는다. 여기서 바로 만들면 그사이 연결이 끊겼을 때 잡는 곳이 없어 워커가 죽는다
                log.error("소비자 그룹이 없어 다시 만든다: %s", e)
                recover = True
            except (redis.ConnectionError, redis.TimeoutError) as e:
                # TimeoutError는 ConnectionError의 하위 클래스가 아니라 따로 잡는다
                log.error("Redis 연결 실패, 1초 뒤 다시 시도: %r", e)
                recover = True
                self.sleep(1.0)
