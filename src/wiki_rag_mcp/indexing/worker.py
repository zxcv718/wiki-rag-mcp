"""인덱싱 이벤트를 Redis Streams에서 받아 처리하는 소비자 (ADR-09, ADR-19, 5장 "장애 대응").

- 스트림(파티션)마다 소비자를 하나만 둔다. 같은 문서의 이벤트는 늘 같은 스트림으로 오므로 한 번에 하나씩
  처리된다. 워커를 늘릴 때는 파티션을 나눠 맡기고(--partitions), 파티션 수를 바꿀 때는 기존 워커를 먼저 멈춘다.
- 처리에 성공하면 XACKDEL ... ACKED로 확인과 삭제를 한 번에 한다. 스트림에는 처리 안 된 이벤트만 남는다.
- 실패하면 지수 백오프로 최대 3회 재시도하고, 그래도 실패하면 DLQ 스트림으로 옮긴다. 재시도하는 동안 같은
  스트림의 뒤 이벤트는 기다린다. 무한 재시도가 뒤 이벤트를 영영 막지 않게 하려고 횟수를 제한한다.
- DLQ는 10분마다 원래 스트림에 다시 넣는다. 처리가 멱등이라 여러 번 들어가도 결과가 같고, 권한 회수가 야간
  배치까지 밀리지 않는다. 같은 이벤트가 계속 실패하면 알림 로그를 남긴다.
- 시작할 때, 이전 소비자가 읽고 확인하지 못한 채 죽어 남은 이벤트를 가져와 먼저 처리한다.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import redis

from wiki_rag_mcp.indexing.events import GROUP, STREAM_PREFIX, Event, dlq_name, parse_event, stream_name
from wiki_rag_mcp.indexing.incremental import Applied

log = logging.getLogger("wiki_rag_mcp.indexing.worker")

RETRIES = 3
BACKOFF_SECONDS = 0.5
REQUEUE_EVERY_SECONDS = 600.0
ALERT_AFTER = 3  # DLQ에 이만큼 들어간 이벤트는 사람이 봐야 한다
_DLQ_ONLY_FIELDS = ("error", "failed_at")


class StreamWorker:
    def __init__(self, client: redis.Redis, handle: Callable[[Event], Applied], partitions: Iterable[int], *,
                 prefix: str = STREAM_PREFIX, group: str = GROUP, consumer: str | None = None,
                 retries: int = RETRIES, backoff: float = BACKOFF_SECONDS,
                 requeue_every: float = REQUEUE_EVERY_SECONDS, alert_after: int = ALERT_AFTER,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        parts = sorted(set(partitions))
        self.client = client
        self.handle = handle
        self.streams = {stream_name(p, prefix): dlq_name(p, prefix) for p in parts}
        self.group = group
        # 재시작해도 같은 이름을 쓰게 파티션으로 이름을 만든다
        self.consumer = consumer or "indexer-" + "-".join(map(str, parts))
        self.retries, self.backoff = retries, backoff
        self.requeue_every, self.alert_after = requeue_every, alert_after
        self.sleep, self.clock = sleep, clock
        self._next_requeue = clock() + requeue_every

    def ensure_groups(self) -> None:
        # id 0으로 만들어, 워커가 처음 뜨기 전에 발행된 이벤트도 읽는다
        for stream in self.streams:
            try:
                self.client.xgroup_create(stream, self.group, id="0", mkstream=True)
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

    def recover_pending(self) -> int:
        """확인되지 않은 채 남은 이벤트를 이 소비자로 가져와 처리한다.

        스트림마다 소비자가 하나뿐이라, 남아 있는 것은 모두 이전 소비자가 처리하다 멈춘 이벤트다.
        """
        handled = 0
        for stream in self.streams:
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
        replies = self.client.xreadgroup(self.group, self.consumer, dict.fromkeys(self.streams, ">"), count=10,
                                         block=block_ms)
        items = replies.items() if isinstance(replies, dict) else (replies or [])
        handled = 0
        for stream, entries in items:
            for entry_id, fields in entries:
                self.process(stream, entry_id, fields)
                handled += 1
        return handled

    def process(self, stream: str, entry_id: str, fields: dict[str, str]) -> bool:
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

    def _log_applied(self, event: Event, applied: Applied) -> None:
        latency: Any = "-"
        if event.created_at is not None:
            latency = int((datetime.now(UTC) - event.created_at).total_seconds() * 1000)
        log.info("%s doc_id=%s revision=%d embedded=%d reused=%d latency_ms=%s", applied.outcome, event.doc_id,
                 applied.revision, applied.embedded, applied.reused, latency)

    def run(self, stop: threading.Event | None = None, block_ms: int = 1000) -> None:
        stop = stop or threading.Event()
        self.ensure_groups()
        self.recover_pending()
        while not stop.is_set():
            try:
                if self.clock() >= self._next_requeue:
                    self.requeue_dlq()
                    self._next_requeue = self.clock() + self.requeue_every
                self.poll(block_ms)
            except redis.ResponseError as e:
                if "NOGROUP" not in str(e):
                    raise
                # Redis가 데이터 없이 다시 떴다. 그룹을 다시 만들고, 잃은 이벤트는 야간 정합성 배치가 찾는다
                log.error("소비자 그룹이 없어 다시 만든다: %s", e)
                self.ensure_groups()
            except redis.ConnectionError as e:
                log.error("Redis 연결 실패, 1초 뒤 다시 시도: %s", e)
                self.sleep(1.0)
