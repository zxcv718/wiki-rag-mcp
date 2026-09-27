"""PostgreSQL + pgvector 검색 저장소 (ADR-02 "단순화할 때").

M2 판정에서 하이브리드 검색이 보류로 나와(ADR-20), 키워드 검색 없이 벡터 검색만 하는 저장소로 옮겼다.
검색 인덱스는 위키 DB와 분리된 데이터베이스에 둔다(ADR-01).

권한은 두 필드(ADR-21)에 "사용자 principal 중 하나라도 포함"을 배열 겹침(&&)으로 걸고 AND로 묶는다.
빈 배열은 어떤 배열과도 겹치지 않으므로 권한 필드가 빈 문서는 아무에게도 나오지 않는다(4장 권한 모델).

pgvector의 HNSW 인덱스는 필터를 인덱스 스캔 뒤에 적용한다. 그래서 iterative scan(0.8.0 이상)을 켜서 조건에
맞는 결과가 k개 모일 때까지 더 스캔하게 한다. 이 스캔도 hnsw.max_scan_tuples에서 멈추므로, 4장의 결과 수
테스트로 k개가 나오는지 확인한다.
"""

import re
import threading
from datetime import datetime
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.rows import dict_row

from wiki_rag_mcp.config import EMBEDDING_DIM, Settings
from wiki_rag_mcp.search.filters import principal_set

_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_COLUMNS = ["chunk_id", "doc_id", "chunk_index", "space", "title", "section_path", "text", "url", "version",
            "revision", "updated_at", "section_hash", "space_principals", "restricted_principals", "classification",
            "embedding_model", "embedding_revision", "embedding_dtype"]
# HNSW 매개변수는 OpenSearch 매핑과 같게 둔다. 옮긴 뒤 같은 골든셋으로 다시 재서 비교하기 위해서다 (ADR-02)
HNSW_M, HNSW_EF_CONSTRUCTION = 16, 128
EF_SEARCH_MIN = 40  # pgvector 기본값. k가 더 크면 k로 올린다


def table_name(alias: str) -> str:
    name = alias.replace("-", "_")
    if not _TABLE.match(name):
        raise ValueError(f"잘못된 테이블 이름: {alias!r}")
    return name


class PgStore:
    def __init__(self, conn: psycopg.Connection, alias: str):
        self.conn = conn
        self.table = table_name(alias)
        self.alias = alias
        self._lock = threading.Lock()  # 연결 하나를 여러 스레드가 나눠 쓰지 않게 한다

    @classmethod
    def from_settings(cls, settings: Settings) -> "PgStore":
        conn = psycopg.connect(settings.database_url, autocommit=True)
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        return cls(conn, settings.index_alias)

    def _t(self) -> sql.Identifier:
        return sql.Identifier(self.table)

    def ensure_index(self, version: int = 1, dim: int = EMBEDDING_DIM) -> str:
        """테이블과 인덱스를 만든다. version은 OpenSearch 인덱스와 인터페이스를 맞추려고 받지만 쓰지 않는다."""
        t = self._t()
        with self._lock:
            self.conn.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {t} (
                    chunk_id text PRIMARY KEY,
                    doc_id text NOT NULL,
                    chunk_index integer NOT NULL,
                    space text NOT NULL,
                    title text NOT NULL,
                    section_path text[] NOT NULL,
                    text text NOT NULL,
                    url text NOT NULL,
                    version integer NOT NULL,
                    revision bigint NOT NULL,
                    updated_at timestamptz NOT NULL,
                    section_hash text NOT NULL,
                    space_principals text[] NOT NULL,
                    restricted_principals text[] NOT NULL,
                    classification text NOT NULL,
                    embedding vector({dim}) NOT NULL,
                    embedding_model text NOT NULL,
                    embedding_revision text NOT NULL,
                    embedding_dtype text NOT NULL
                )""").format(t=t, dim=sql.Literal(dim)))
            self.conn.execute(sql.SQL(
                "CREATE INDEX IF NOT EXISTS {i} ON {t} USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = {m}, ef_construction = {ef})").format(
                i=sql.Identifier(f"{self.table}_embedding"), t=t, m=sql.Literal(HNSW_M),
                ef=sql.Literal(HNSW_EF_CONSTRUCTION)))
            for column in ("doc_id", "updated_at"):
                self.conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {i} ON {t} ({c})").format(
                    i=sql.Identifier(f"{self.table}_{column}"), t=t, c=sql.Identifier(column)))
        return self.table

    def drop(self) -> None:
        with self._lock:
            self.conn.execute(sql.SQL("DROP TABLE IF EXISTS {t}").format(t=self._t()))

    def index_chunks(self, docs: list[dict[str, Any]], refresh: bool = True) -> int:
        columns = [*_COLUMNS, "embedding"]
        statement = sql.SQL(
            "INSERT INTO {t} ({cols}) VALUES ({vals}) ON CONFLICT (chunk_id) DO UPDATE SET {updates}").format(
            t=self._t(), cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
            vals=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            updates=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
                                       for c in columns if c != "chunk_id"))
        rows = [[d[c] for c in _COLUMNS] + [np.asarray(d["embedding"], dtype=np.float32)] for d in docs]
        with self._lock, self.conn.cursor() as cur:
            cur.executemany(statement, rows)
        return len(rows)

    def delete_document(self, doc_id: str, refresh: bool = True) -> None:
        with self._lock:
            self.conn.execute(sql.SQL("DELETE FROM {t} WHERE doc_id = %s").format(t=self._t()), [doc_id])

    def delete_stale_chunks(self, doc_id: str, keep_ids: list[str], refresh: bool = True) -> None:
        with self._lock:
            self.conn.execute(sql.SQL("DELETE FROM {t} WHERE doc_id = %s AND NOT (chunk_id = ANY(%s))").format(
                t=self._t()), [doc_id, keep_ids])

    @staticmethod
    def _where(principals: list[str], space: str | None, updated_after: datetime | None
               ) -> tuple[sql.Composable, list[Any]]:
        allowed = principal_set(principals)
        clauses = [sql.SQL("space_principals && %s"), sql.SQL("restricted_principals && %s")]
        params: list[Any] = [allowed, allowed]
        if space:
            clauses.append(sql.SQL("space = %s"))
            params.append(space)
        if updated_after:
            clauses.append(sql.SQL("updated_at >= %s"))
            params.append(updated_after)
        return sql.SQL(" AND ").join(clauses), params

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "updated_at": row["updated_at"].isoformat(), "section_path": list(row["section_path"])}

    def knn_search(self, vector: list[float], principals: list[str], k: int, *, space: str | None = None,
                   updated_after: datetime | None = None) -> list[dict[str, Any]]:
        """권한 조건을 WHERE에 건 벡터 검색. 점수는 코사인 유사도다."""
        where, params = self._where(principals, space, updated_after)
        query = sql.SQL(
            "SELECT {cols}, 1 - (embedding <=> %s) AS score FROM {t} WHERE {where} "
            "ORDER BY embedding <=> %s LIMIT %s").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, _COLUMNS)), t=self._t(), where=where)
        vec = np.asarray(vector, dtype=np.float32)
        with self._lock, self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            # strict_order는 거리 순서를 지키며 조건에 맞는 결과가 k개 모일 때까지 더 스캔한다
            cur.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            cur.execute(sql.SQL("SET LOCAL hnsw.ef_search = {n}").format(n=sql.Literal(max(EF_SEARCH_MIN, k))))
            cur.execute(query, [vec, *params, vec, k])
            return [self._row(r) for r in cur.fetchall()]

    def recent_changes(self, principals: list[str], since: datetime, *, space: str | None = None,
                       size: int = 20) -> list[dict[str, Any]]:
        """since 이후 수정된 문서를 최신순으로 문서당 하나씩 돌려준다."""
        where, params = self._where(principals, space, since)
        columns = [c for c in _COLUMNS if c != "text"]
        query = sql.SQL(
            "SELECT * FROM (SELECT DISTINCT ON (doc_id) {cols} FROM {t} WHERE {where} ORDER BY doc_id, chunk_index) "
            "AS latest ORDER BY updated_at DESC, doc_id LIMIT %s").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)), t=self._t(), where=where)
        with self._lock, self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, [*params, size])
            return [self._row(r) for r in cur.fetchall()]
