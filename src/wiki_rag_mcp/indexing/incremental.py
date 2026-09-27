"""이벤트 하나로 문서 하나의 인덱스를 위키의 현재 상태에 맞춘다 (ADR-19, ADR-10).

순서는 아래와 같다.

1. 인덱스에 기록된 revision이 이벤트의 revision 이상이면 이미 반영한 것이라 건너뛴다.
2. 이벤트는 신호로만 쓰고, 위키에서 현재 상태를 캐시 없이 다시 읽는다. 그래서 이벤트가 중복되거나 순서가
   바뀌어도 결과가 같다.
3. 삭제됐으면 청크를 지우고 삭제 상태를 남긴다.
4. 기존 청크의 권한·등급을 먼저 바꿔 커밋한다. 다음 단계의 임베딩이 실패해도 권한 회수는 이미 적용된다.
5. 섹션 해시로 바뀐 청크만 임베딩하고, 청크 교체와 상태 기록을 한 트랜잭션으로 한다. 상태를 마지막에 쓰므로
   중간에 실패하면 재시도가 "이미 반영됨"으로 건너뛰지 않는다.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from wiki_rag_mcp.indexing.chunker import CountTokens, chunk_document
from wiki_rag_mcp.indexing.events import Event
from wiki_rag_mcp.indexing.indexer import DocumentEncoder, embedding_input, to_index_doc
from wiki_rag_mcp.models import Document
from wiki_rag_mcp.search.backend import SearchStore
from wiki_rag_mcp.wiki.source import DocState, IndexSource

REREAD_ATTEMPTS = 3
REREAD_DELAY_SECONDS = 0.5


class StaleRead(RuntimeError):
    """위키에서 읽은 revision이 이벤트보다 낮다. 커밋 전에 읽었거나 복제 지연이 있다는 뜻이라 재시도한다."""


@dataclass(frozen=True)
class Applied:
    doc_id: str
    outcome: str  # "skipped", "deleted", "indexed"
    revision: int
    embedded: int = 0  # 새로 임베딩한 청크 수
    reused: int = 0  # 임베딩을 다시 쓴 청크 수


def read_state(source: IndexSource, event: Event, *, attempts: int = REREAD_ATTEMPTS,
               delay: float = REREAD_DELAY_SECONDS, sleep: Callable[[float], None] = time.sleep) -> DocState:
    state = source.state(event.doc_id)
    for _ in range(attempts - 1):
        if state.revision is None or state.revision >= event.revision:
            return state
        sleep(delay)
        state = source.state(event.doc_id)
    if state.revision is None or state.revision >= event.revision:
        return state
    raise StaleRead(f"{event.doc_id}: 위키 revision {state.revision} < 이벤트 revision {event.revision}")


def reusable(old: dict[str, Any], doc: Document, encoder: DocumentEncoder) -> bool:
    """저장된 임베딩을 그대로 써도 되는가 (ADR-10).

    청크 id는 섹션 제목 경로와 본문의 해시라, 같은 id면 청크 본문도 같다. 다만 임베딩 입력에는 맥락 헤더
    (스페이스 > 문서 제목 > 섹션 경로)가 붙으므로 문서 제목이나 스페이스가 바뀌면 다시 임베딩한다.
    모델이 바뀌어도 다시 임베딩한다. 스페이스 표시 이름은 바꾸는 API가 없어서 비교하지 않는다.
    """
    return (old["title"] == doc.title and old["space"] == doc.space
            and old["embedding_model"] == encoder.model_name and old["embedding_revision"] == encoder.revision
            and old["embedding_dtype"] == encoder.dtype)


def build_rows(doc: Document, space_title: str, existing: dict[str, dict[str, Any]], encoder: DocumentEncoder,
               count: CountTokens) -> tuple[list[dict[str, Any]], int]:
    """문서의 새 청크 행을 만든다. 바뀐 청크만 임베딩하고, 임베딩한 청크 수를 함께 돌려준다."""
    chunks = chunk_document(doc.doc_id, doc.body, doc.title, count)
    keep = {cid for cid, old in existing.items() if reusable(old, doc, encoder)}
    todo = [c for c in chunks if c.chunk_id not in keep]
    fresh = encoder.encode_documents([embedding_input(doc, c, space_title) for c in todo]) if todo else []
    vectors: dict[str, Any] = dict(zip((c.chunk_id for c in todo), fresh, strict=True))
    rows = []
    for c in chunks:
        vector = vectors[c.chunk_id] if c.chunk_id in vectors else existing[c.chunk_id]["embedding"]
        rows.append(to_index_doc(doc, c, np.asarray(vector), encoder))
    return rows, len(todo)


def apply_event(event: Event, source: IndexSource, store: SearchStore, encoder: DocumentEncoder,
                count: CountTokens, *, sleep: Callable[[float], None] = time.sleep) -> Applied:
    stored = store.doc_state(event.doc_id)
    if stored is not None and stored.revision >= event.revision:
        return Applied(event.doc_id, "skipped", stored.revision)

    state = read_state(source, event, sleep=sleep)
    if state.document is None:
        # 위키에 한 번도 없던 문서(정합성 배치가 찾은 인덱스의 고아 문서)는 이벤트의 revision을 남긴다
        revision = max(state.revision or 0, event.revision)
        store.mark_deleted(event.doc_id, revision)
        return Applied(event.doc_id, "deleted", revision)

    doc = state.document
    store.update_permissions(doc.doc_id, doc.space_principals, doc.restricted_principals, doc.classification)
    rows, embedded = build_rows(doc, source.space_title(doc.space), store.existing_chunks(doc.doc_id), encoder, count)
    store.replace_document(doc.doc_id, rows, doc.revision)
    return Applied(doc.doc_id, "indexed", doc.revision, embedded=embedded, reused=len(rows) - embedded)
