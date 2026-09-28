"""검색 저장소를 연다. 서버, 색인, 평가가 모두 이 함수로 저장소를 얻는다 (ADR-22)."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from wiki_rag_mcp.config import EMBEDDING_DIM, Settings

if TYPE_CHECKING:
    from wiki_rag_mcp.search.pg_store import StoredState


class SearchStore(Protocol):
    """검색 저장소가 갖춰야 할 연산. 권한 필터는 저장소가 검색 안에서 건다 (ADR-07)."""

    alias: str

    def ensure_index(self, dim: int = EMBEDDING_DIM) -> str: ...

    def drop(self) -> None: ...

    def index_chunks(self, docs: list[dict[str, Any]]) -> int: ...

    def knn_search(self, vector: list[float], principals: list[str], k: int, *, space: str | None = None,
                   updated_after: datetime | None = None) -> list[dict[str, Any]]: ...

    def recent_changes(self, principals: list[str], since: datetime, *, space: str | None = None,
                       size: int = 20) -> list[dict[str, Any]]: ...

    # 증분 색인 (ADR-19). 문서별 상태를 청크와 함께 관리한다

    def doc_state(self, doc_id: str) -> "StoredState | None": ...

    def doc_states(self) -> "dict[str, StoredState]": ...

    def indexed_doc_ids(self) -> set[str]: ...

    def update_permissions(self, doc_id: str, space_principals: list[str] | tuple[str, ...],
                           restricted_principals: list[str] | tuple[str, ...], classification: str) -> int: ...

    def existing_chunks(self, doc_id: str) -> dict[str, dict[str, Any]]: ...

    def replace_document(self, doc_id: str, docs: list[dict[str, Any]], revision: int) -> bool: ...

    def mark_deleted(self, doc_id: str, revision: int) -> bool: ...


def open_store(settings: Settings, alias: str | None = None, readers: int = 0) -> SearchStore:
    """PostgreSQL + pgvector 저장소를 연다. alias는 테이블 이름이다(하이픈은 밑줄로 바뀐다). readers는 검색 읽기에만
    쓰는 연결 수로, 검색 서버만 준다(PgStore.from_settings)."""
    from dataclasses import replace

    from wiki_rag_mcp.search.pg_store import PgStore

    return PgStore.from_settings(replace(settings, index_alias=alias or settings.index_alias), readers)
