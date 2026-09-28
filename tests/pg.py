"""통합 테스트가 쓰는 PostgreSQL + pgvector 저장소 도우미. `docker compose up -d postgres`로 띄운 뒤 쓴다."""

import uuid

from tests.services import unavailable
from wiki_rag_mcp.config import Settings


def new_store(prefix: str, dim: int, readers: int = 0):
    """테스트마다 새 테이블을 만든다. PostgreSQL이 떠 있지 않으면 테스트를 건너뛴다."""
    import psycopg

    from wiki_rag_mcp.search.pg_store import PgStore

    try:
        store = PgStore.from_settings(Settings(index_alias=f"test_{prefix}_{uuid.uuid4().hex[:8]}"), readers)
    except psycopg.OperationalError:
        unavailable("로컬 PostgreSQL이 떠 있지 않다 (docker compose up -d postgres)")
    store.ensure_index(dim=dim)
    return store


def count(store, where: str = "TRUE", params: tuple = ()) -> int:
    return store.conn.execute(f"SELECT count(*) FROM {store.table} WHERE {where}", params).fetchone()[0]
