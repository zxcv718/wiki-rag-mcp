"""인덱싱 이벤트의 형식과 스트림 이름 (ADR-09, ADR-19, wiki-service/README.md "이벤트").

이벤트는 "이 문서가 바뀌었다"는 신호일 뿐이고 내용은 담지 않는다. 인덱서는 revision으로 순서만 판단하고
상태는 위키에서 다시 읽는다.
"""

import zlib
from dataclasses import dataclass
from datetime import UTC, datetime

from wiki_rag_mcp.models import valid_doc_id, valid_user_id

STREAM_PREFIX = "wiki:events"
MEMBERSHIP_STREAM = "wiki:membership"  # 그룹 캐시 무효화용 (ADR-08)
GROUP = "indexer"
DOC_EVENT_TYPES = frozenset({"CONTENT_CHANGED", "ACL_CHANGED", "DELETED", "RECONCILE"})


@dataclass(frozen=True)
class Event:
    doc_id: str
    revision: int
    type: str
    created_at: datetime | None = None  # 아웃박스에 기록된 시각. 인덱싱 지연을 여기서부터 잰다


def partition(doc_id: str, partitions: int) -> int:
    """같은 문서의 이벤트가 늘 같은 스트림으로 가게 한다. 위키 서비스(Java CRC32)와 같은 계산이어야 한다."""
    return zlib.crc32(doc_id.encode("utf-8")) % partitions


def stream_name(p: int, prefix: str = STREAM_PREFIX) -> str:
    return f"{prefix}:{p}"


def dlq_name(p: int, prefix: str = STREAM_PREFIX) -> str:
    return f"{prefix}:{p}:dlq"


def parse_event(fields: dict[str, str]) -> Event:
    """스트림 항목을 이벤트로 읽는다. 형식이 틀리면 ValueError."""
    doc_id = fields.get("doc_id", "")
    if not valid_doc_id(doc_id):
        raise ValueError(f"잘못된 doc_id: {doc_id!r}")
    kind = fields.get("type", "")
    if kind not in DOC_EVENT_TYPES:
        raise ValueError(f"모르는 이벤트 종류: {kind!r}")
    revision = int(fields["revision"])
    if revision < 1:
        raise ValueError(f"잘못된 revision: {revision}")
    created = fields.get("created_at")
    return Event(doc_id, revision, kind, datetime.fromisoformat(created) if created else None)


def event_fields(event: Event) -> dict[str, str]:
    created = event.created_at or datetime.now(UTC)
    return {"doc_id": event.doc_id, "revision": str(event.revision), "type": event.type,
            "created_at": created.isoformat()}


def parse_membership(fields: dict[str, str]) -> str:
    """멤버십 이벤트에서 사용자 id를 읽는다. 형식이 틀리면 ValueError."""
    if fields.get("type") != "MEMBERSHIP_CHANGED":
        raise ValueError(f"모르는 멤버십 이벤트 종류: {fields.get('type')!r}")
    user_id = fields.get("user_id", "")
    if not valid_user_id(user_id):
        raise ValueError(f"잘못된 user_id: {user_id!r}")
    return user_id
