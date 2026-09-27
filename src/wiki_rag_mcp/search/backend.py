"""검색 저장소를 연다. 서버, 색인, 평가가 모두 이 함수로 저장소를 얻는다 (ADR-22)."""

from datetime import datetime
from typing import Any, Protocol

from wiki_rag_mcp.config import EMBEDDING_DIM, Settings


class SearchStore(Protocol):
    """검색 저장소가 갖춰야 할 연산. 권한 필터는 저장소가 검색 안에서 건다 (ADR-07)."""

    alias: str

    def ensure_index(self, dim: int = EMBEDDING_DIM) -> str: ...

    def drop(self) -> None: ...

    def index_chunks(self, docs: list[dict[str, Any]], refresh: bool = True) -> int: ...

    def delete_document(self, doc_id: str, refresh: bool = True) -> None: ...

    def delete_stale_chunks(self, doc_id: str, keep_ids: list[str], refresh: bool = True) -> None: ...

    def knn_search(self, vector: list[float], principals: list[str], k: int, *, space: str | None = None,
                   updated_after: datetime | None = None) -> list[dict[str, Any]]: ...

    def recent_changes(self, principals: list[str], since: datetime, *, space: str | None = None,
                       size: int = 20) -> list[dict[str, Any]]: ...


def open_store(settings: Settings, alias: str | None = None) -> SearchStore:
    """PostgreSQL + pgvector 저장소를 연다. alias는 테이블 이름이다(하이픈은 밑줄로 바뀐다)."""
    from dataclasses import replace

    from wiki_rag_mcp.search.pg_store import PgStore

    return PgStore.from_settings(replace(settings, index_alias=alias or settings.index_alias))
