"""인덱싱 명령.

- `wiki-rag-index`: 위키 전체를 임베딩해 검색 인덱스에 넣는다. 평가용 인덱스와 처음 색인할 때 쓴다.
- `wiki-rag-worker`: 위키 이벤트를 받아 바뀐 문서만 반영하고, 멤버십 이벤트로 그룹 캐시를 무효화한다
  (ADR-08, ADR-09, ADR-19).
- `wiki-rag-reconcile`: 야간 정합성 배치. 위키와 어긋난 문서의 이벤트를 다시 넣는다 (5장 "장애 대응").
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from wiki_rag_mcp import telemetry
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.search.backend import open_store


def _wiki(settings: Settings):
    from wiki_rag_mcp.wiki.http import HttpWikiSource

    return HttpWikiSource(settings.wiki_api_url, settings.wiki_service_token)


def _redis(settings: Settings):
    import redis

    # 워커는 XREADGROUP BLOCK 1초로 기다린다. Redis가 응답 없이 멈추면(연결이 반만 열린 채 남는 경우) 타임아웃이
    # 없을 때 영영 기다려, 권한 회수가 워커를 재시작할 때까지 반영되지 않는다
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=10,
                                socket_connect_timeout=5, socket_keepalive=True, health_check_interval=30)


def main(argv: list[str] | None = None) -> None:
    from wiki_rag_mcp.indexing.embedder import Embedder, TokenCounter
    from wiki_rag_mcp.indexing.indexer import index_all
    from wiki_rag_mcp.wiki.files import FileWikiSource

    parser = argparse.ArgumentParser(description="위키 전체를 임베딩해 검색 인덱스에 넣는다.")
    parser.add_argument("--source", choices=["file", "wiki"], default="file",
                        help="file: data/wiki의 가상 위키 파일, wiki: Spring 위키 서비스 (기본: file)")
    parser.add_argument("--wiki-dir", type=Path, help="가상 위키 디렉터리 (기본: WIKI_DIR 또는 data/wiki)")
    parser.add_argument("--reset", action="store_true", help="인덱스를 지우고 새로 만든다")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    source = _wiki(settings) if args.source == "wiki" else FileWikiSource(args.wiki_dir or settings.wiki_dir)
    store = open_store(settings)
    if args.reset:
        store.drop()
    index_name = store.ensure_index()
    embedder = Embedder()
    stats = index_all(source, store, embedder, TokenCounter())
    print(f"색인 완료: 문서 {stats.documents}개, 청크 {stats.chunks}개, {stats.seconds:.1f}초 "
          f"({index_name}, {embedder.model_name}@{embedder.revision[:8]}, {embedder.dtype}, {embedder.device})")


def worker_main(argv: list[str] | None = None) -> None:
    from wiki_rag_mcp.auth.groups import invalidate
    from wiki_rag_mcp.indexing.embedder import Embedder, TokenCounter
    from wiki_rag_mcp.indexing.incremental import apply_event
    from wiki_rag_mcp.indexing.worker import StreamWorker

    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="위키 이벤트를 받아 바뀐 문서만 검색 인덱스에 반영한다.")
    parser.add_argument("--partitions", default=None,
                        help=f"맡을 스트림 번호, 쉼표로 구분 (기본: 전체 0~{settings.event_partitions - 1}). "
                             "한 파티션은 워커 하나만 맡는다")
    args = parser.parse_args(argv)
    partitions = ([int(p) for p in args.partitions.split(",")] if args.partitions
                  else list(range(settings.event_partitions)))
    if any(not 0 <= p < settings.event_partitions for p in partitions):
        parser.error(f"파티션 번호는 0~{settings.event_partitions - 1}이어야 한다")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 이벤트마다 위키 요청 로그가 찍히지 않게 한다
    telemetry.setup("wiki-rag-worker")
    source, store = _wiki(settings), open_store(settings)
    store.ensure_index()
    embedder, count = Embedder(), TokenCounter()
    client = _redis(settings)
    worker = StreamWorker(client, lambda e: apply_event(e, source, store, embedder, count), partitions,
                          membership=lambda user_id: invalidate(client, user_id))
    worker.observe_queues()

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logging.info("워커 시작: 파티션 %s, 소비자 %s", partitions, worker.consumer)
    worker.run(stop)


def reconcile_main(argv: list[str] | None = None) -> None:
    from wiki_rag_mcp.indexing.events import event_fields, partition, stream_name
    from wiki_rag_mcp.indexing.reconcile import reconcile

    parser = argparse.ArgumentParser(
        description="위키와 검색 인덱스의 문서별 revision을 비교해 어긋난 문서를 다시 반영한다.")
    parser.add_argument("--dry-run", action="store_true", help="이벤트를 넣지 않고 어긋난 문서만 보여 준다")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    client = _redis(settings)

    def publish(event):
        if not args.dry_run:
            client.xadd(stream_name(partition(event.doc_id, settings.event_partitions)), event_fields(event))

    report = reconcile(_wiki(settings), open_store(settings), publish)
    for event in report.republished:
        print(f"다시 넣음: {event.doc_id} revision {event.revision}")
    for line in report.anomalies:
        print(f"확인 필요: {line}")
    verb = "넣을 예정(dry run)" if args.dry_run else "넣음"
    print(f"문서 {report.checked}개 확인, 이벤트 {len(report.republished)}개 {verb}, "
          f"확인 필요 {len(report.anomalies)}개")
    # 이벤트로 고칠 수 없는 어긋남은 cron이 알림을 보낼 수 있게 실패로 끝낸다
    sys.exit(1 if report.anomalies else 0)


if __name__ == "__main__":
    main()
