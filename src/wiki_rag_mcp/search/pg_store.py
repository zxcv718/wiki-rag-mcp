"""PostgreSQL + pgvector 검색 저장소 (ADR-02 "단순화할 때").

M2 판정에서 하이브리드 검색이 보류로 나와(ADR-20), 키워드 검색 없이 벡터 검색만 하는 저장소로 옮겼다.
검색 인덱스는 위키 DB와 분리된 데이터베이스에 둔다(ADR-01).

권한은 두 필드(ADR-21)에 "사용자 principal 중 하나라도 포함"을 배열 겹침(&&)으로 걸고 AND로 묶는다.
빈 배열은 어떤 배열과도 겹치지 않으므로 권한 필드가 빈 문서는 아무에게도 나오지 않는다(4장 권한 모델).

pgvector의 HNSW 인덱스는 필터를 인덱스 스캔 뒤에 적용한다. 그래서 iterative scan(0.8.0 이상)을 켜서 조건에
맞는 결과가 k개 모일 때까지 더 스캔하게 한다. 이 스캔도 hnsw.max_scan_tuples에서 멈추므로, 4장의 결과 수
테스트로 k개가 나오는지 확인한다.

청크 테이블 옆에 문서별 상태 테이블({청크 테이블}_doc_state)을 둔다(ADR-19). 인덱서는 이벤트의 revision을
여기에 적힌 값과 비교해 이미 반영한 이벤트를 건너뛴다. 청크와 같은 데이터베이스에 있어서, 청크 교체와
상태 기록을 한 트랜잭션으로 묶을 수 있다.
"""

import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from wiki_rag_mcp.config import EMBEDDING_DIM, Settings
from wiki_rag_mcp.search.filters import principal_set

_TABLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_COLUMNS = ["chunk_id", "doc_id", "chunk_index", "space", "title", "section_path", "text", "url", "version",
            "revision", "updated_at", "section_hash", "space_principals", "restricted_principals", "classification",
            "embedding_model", "embedding_revision", "embedding_dtype"]
# HNSW 매개변수는 M2 판정 때의 OpenSearch 매핑과 같은 값이다. 옮긴 뒤 같은 골든셋으로 비교했다 (ADR-22)
HNSW_M, HNSW_EF_CONSTRUCTION = 16, 128
EF_SEARCH_MIN = 40  # pgvector 기본값. k가 더 크면 k로 올린다


@dataclass(frozen=True)
class StoredState:
    revision: int
    deleted: bool


def table_name(alias: str) -> str:
    name = alias.replace("-", "_")
    if not _TABLE.fullmatch(name):
        raise ValueError(f"잘못된 테이블 이름: {alias!r}")
    return name


class PgStore:
    def __init__(self, conn: psycopg.Connection, alias: str, readers: ConnectionPool | None = None):
        self.conn = conn
        self.table = table_name(alias)
        self.alias = alias
        self._lock = threading.Lock()  # 연결 하나를 여러 스레드가 나눠 쓰지 않게 한다
        self._readers = readers

    @classmethod
    def from_settings(cls, settings: Settings, readers: int = 0) -> "PgStore":
        """readers가 있으면 검색 읽기(knn_search, recent_changes)만 그만큼의 연결 풀에서 빌려 쓴다(검색 서버).

        연결 하나를 잠가 나눠 쓰면 요청이 몰릴 때 검색이 차례를 기다린다(experiments/m5-speedup, 동시 50에서 벡터 검색
        p95 399ms). 풀은 빌려줄 때 연결이 살아 있는지 확인하고, 끊긴 연결은 새로 연다. 쓰기는 늘 연결 하나다.
        """
        conn = psycopg.connect(settings.database_url, autocommit=True)
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        pool = None
        if readers:
            pool = ConnectionPool(settings.database_url, min_size=readers, max_size=readers, name="search-readers",
                                  kwargs={"autocommit": True}, configure=register_vector,
                                  check=ConnectionPool.check_connection, open=True)
        return cls(conn, settings.index_alias, pool)

    @contextmanager
    def _reading(self) -> Iterator[psycopg.Connection]:
        """검색에 쓸 연결. 읽기 풀이 있으면 하나를 빌리고, 없으면 쓰기 연결을 잠가서 쓴다."""
        if self._readers is None:
            with self._lock:
                yield self.conn
        else:
            with self._readers.connection() as conn:
                yield conn

    def _t(self) -> sql.Identifier:
        return sql.Identifier(self.table)

    def _s(self) -> sql.Identifier:
        return sql.Identifier(f"{self.table}_doc_state")

    def ensure_index(self, dim: int = EMBEDDING_DIM) -> str:
        """테이블과 인덱스를 만든다. 이미 있으면 그대로 둔다."""
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
            self.conn.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {s} (
                    doc_id text PRIMARY KEY,
                    revision bigint NOT NULL,
                    deleted boolean NOT NULL,
                    indexed_at timestamptz NOT NULL DEFAULT now()
                )""").format(s=self._s()))
        return self.table

    def drop(self) -> None:
        with self._lock:
            self.conn.execute(sql.SQL("DROP TABLE IF EXISTS {t}, {s}").format(t=self._t(), s=self._s()))

    def _upsert(self) -> sql.Composable:
        columns = [*_COLUMNS, "embedding"]
        return sql.SQL(
            "INSERT INTO {t} ({cols}) VALUES ({vals}) ON CONFLICT (chunk_id) DO UPDATE SET {updates}").format(
            t=self._t(), cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
            vals=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            updates=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
                                       for c in columns if c != "chunk_id"))

    @staticmethod
    def _rows(docs: list[dict[str, Any]]) -> list[list[Any]]:
        return [[d[c] for c in _COLUMNS] + [np.asarray(d["embedding"], dtype=np.float32)] for d in docs]

    def index_chunks(self, docs: list[dict[str, Any]]) -> int:
        rows = self._rows(docs)
        with self._lock, self.conn.cursor() as cur:
            cur.executemany(self._upsert(), rows)
        return len(rows)

    # 증분 색인 (ADR-19). 한 문서의 이벤트는 스트림 파티션 덕분에 소비자 하나가 차례로 처리한다.
    # 아래의 revision 비교는 그 가정이 깨졌을 때(스트림 수를 바꾸다 옛 소비자가 남은 경우 등) 인덱스가
    # 옛 상태로 되돌아가지 않게 하는 두 번째 방어선이다.

    def doc_state(self, doc_id: str) -> StoredState | None:
        with self._lock:
            row = self.conn.execute(sql.SQL("SELECT revision, deleted FROM {s} WHERE doc_id = %s").format(
                s=self._s()), [doc_id]).fetchone()
        return StoredState(int(row[0]), bool(row[1])) if row else None

    def doc_states(self) -> dict[str, StoredState]:
        with self._lock:
            rows = self.conn.execute(sql.SQL("SELECT doc_id, revision, deleted FROM {s}").format(
                s=self._s())).fetchall()
        return {r[0]: StoredState(int(r[1]), bool(r[2])) for r in rows}

    def indexed_doc_ids(self) -> set[str]:
        """청크가 있는 문서. 상태 기록 없이 들어간 청크(M3 이전 색인)를 정합성 배치가 찾을 때 쓴다."""
        with self._lock:
            rows = self.conn.execute(sql.SQL("SELECT DISTINCT doc_id FROM {t}").format(t=self._t())).fetchall()
        return {r[0] for r in rows}

    def update_permissions(self, doc_id: str, space_principals: list[str] | tuple[str, ...],
                           restricted_principals: list[str] | tuple[str, ...], classification: str) -> int:
        """기존 청크의 권한·등급만 바꾼다. 임베딩 전에 따로 커밋해, 임베딩이 실패해도 권한 회수는 먼저 적용되게 한다."""
        with self._lock:
            cur = self.conn.execute(sql.SQL(
                "UPDATE {t} SET space_principals = %s, restricted_principals = %s, classification = %s "
                "WHERE doc_id = %s").format(t=self._t()),
                [list(space_principals), list(restricted_principals), str(classification), doc_id])
            return cur.rowcount

    def existing_chunks(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """다시 쓸 수 있는 임베딩을 고르기 위해 문서의 청크를 읽는다 (ADR-10)."""
        columns = ["chunk_id", "title", "space", "embedding", "embedding_model", "embedding_revision",
                   "embedding_dtype"]
        with self._lock, self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL("SELECT {cols} FROM {t} WHERE doc_id = %s").format(
                cols=sql.SQL(", ").join(map(sql.Identifier, columns)), t=self._t()), [doc_id])
            rows = cur.fetchall()
        # pgvector는 벡터를 자체 Vector 객체로 돌려준다
        return {r["chunk_id"]: {**r, "embedding": r["embedding"].to_numpy()} for r in rows}

    def _newer_state_exists(self, cur: psycopg.Cursor, doc_id: str, revision: int) -> bool:
        cur.execute(sql.SQL("SELECT revision FROM {s} WHERE doc_id = %s FOR UPDATE").format(s=self._s()), [doc_id])
        row = cur.fetchone()
        return row is not None and row[0] > revision

    def _write_state(self, cur: psycopg.Cursor, doc_id: str, revision: int, deleted: bool) -> None:
        cur.execute(sql.SQL(
            "INSERT INTO {s} (doc_id, revision, deleted, indexed_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (doc_id) DO UPDATE SET revision = EXCLUDED.revision, deleted = EXCLUDED.deleted, "
            "indexed_at = EXCLUDED.indexed_at").format(s=self._s()), [doc_id, revision, deleted])

    def replace_document(self, doc_id: str, docs: list[dict[str, Any]], revision: int) -> bool:
        """문서의 청크를 docs로 바꾸고 상태를 기록한다. 한 트랜잭션이라 중간에 실패하면 아무것도 바뀌지 않는다.

        상태는 마지막에 쓴다(ADR-19). 더 새로운 revision이 이미 기록돼 있으면 아무것도 쓰지 않고 False.
        """
        rows = self._rows(docs)
        with self._lock, self.conn.transaction(), self.conn.cursor() as cur:
            if self._newer_state_exists(cur, doc_id, revision):
                return False
            cur.executemany(self._upsert(), rows)
            cur.execute(sql.SQL("DELETE FROM {t} WHERE doc_id = %s AND NOT (chunk_id = ANY(%s))").format(
                t=self._t()), [doc_id, [d["chunk_id"] for d in docs]])
            self._write_state(cur, doc_id, revision, deleted=False)
        return True

    def mark_deleted(self, doc_id: str, revision: int) -> bool:
        """문서의 청크를 모두 지우고 삭제 상태를 기록한다.

        revision을 남겨 두어, 늦게 도착한 옛 이벤트가 삭제된 문서를 되살리지 못하게 한다.
        """
        with self._lock, self.conn.transaction(), self.conn.cursor() as cur:
            if self._newer_state_exists(cur, doc_id, revision):
                return False
            cur.execute(sql.SQL("DELETE FROM {t} WHERE doc_id = %s").format(t=self._t()), [doc_id])
            self._write_state(cur, doc_id, revision, deleted=True)
        return True

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
        with self._reading() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
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
        with self._reading() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, [*params, size])
            return [self._row(r) for r in cur.fetchall()]
