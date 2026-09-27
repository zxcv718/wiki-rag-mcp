"""OpenSearch 전용 코드는 이 모듈에만 둔다.

M2 판정에서 ADR-02가 보류나 기각으로 나오면 PostgreSQL + pgvector로 옮기는데(ADR-02 "단순화할 때"),
그때 이 모듈만 바꾸면 되게 하려는 것이다.
"""

from datetime import datetime
from typing import Any

from opensearchpy import OpenSearch, helpers

from wiki_rag_mcp.config import EMBEDDING_DIM, Settings
from wiki_rag_mcp.search.filters import search_filter

_SOURCE_EXCLUDES = ["embedding"]


def index_body(dim: int = EMBEDDING_DIM) -> dict[str, Any]:
    """청크 인덱스의 설정과 매핑. 권한·등급 필드는 M1부터 넣어 나중의 전체 재색인을 피한다."""
    return {
        "settings": {
            "index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0},
            "analysis": {
                "analyzer": {
                    "korean": {
                        "type": "custom",
                        "tokenizer": "nori_tokenizer",
                        "filter": ["nori_part_of_speech", "lowercase"],
                    }
                }
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                "doc_id": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "space": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "korean"},
                "section_path": {"type": "keyword"},
                # BM25용 본문 필드. 하이브리드 검색은 M2 실험 대상이지만 매핑은 지금 확정한다
                "text": {"type": "text", "analyzer": "korean"},
                "url": {"type": "keyword", "index": False},
                "version": {"type": "integer"},
                "revision": {"type": "long"},
                "updated_at": {"type": "date"},
                "section_hash": {"type": "keyword"},
                "space_principals": {"type": "keyword"},
                "restricted_principals": {"type": "keyword"},
                "classification": {"type": "keyword"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dim,
                    # lucene 엔진은 knn 절 안의 filter를 검색 도중에 적용한다(efficient filtering)
                    "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil",
                               "parameters": {"m": 16, "ef_construction": 128}},
                },
                "embedding_model": {"type": "keyword"},
                "embedding_revision": {"type": "keyword"},
                "embedding_dtype": {"type": "keyword"},
            },
        },
    }


class OpenSearchStore:
    def __init__(self, client: OpenSearch, alias: str):
        self.client = client
        self.alias = alias

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenSearchStore":
        return cls(OpenSearch(hosts=[settings.opensearch_url], timeout=30), settings.index_alias)

    def ensure_index(self, version: int = 1, dim: int = EMBEDDING_DIM) -> str:
        """별칭이 없으면 버전이 붙은 인덱스를 만들고 별칭을 건다.

        임베딩 모델을 바꿔 전체 재색인할 때는 새 버전 인덱스를 다 채운 뒤 별칭만 옮긴다.
        """
        if self.client.indices.exists_alias(name=self.alias):
            return next(iter(self.client.indices.get_alias(name=self.alias)))
        name = f"{self.alias}-v{version}"
        self.client.indices.create(index=name, body=index_body(dim))
        self.client.indices.put_alias(index=name, name=self.alias)
        return name

    def drop(self) -> None:
        """별칭이 가리키는 인덱스를 지운다. 테스트와 로컬 초기화용이다."""
        if self.client.indices.exists_alias(name=self.alias):
            for name in self.client.indices.get_alias(name=self.alias):
                self.client.indices.delete(index=name)

    def index_chunks(self, docs: list[dict[str, Any]], refresh: bool = True) -> int:
        actions = ({"_index": self.alias, "_id": d["chunk_id"], "_source": d} for d in docs)
        ok, _ = helpers.bulk(self.client, actions, refresh=refresh)
        return ok

    def delete_document(self, doc_id: str, refresh: bool = True) -> None:
        self.client.delete_by_query(index=self.alias, body={"query": {"term": {"doc_id": doc_id}}},
                                    refresh=refresh)

    def delete_stale_chunks(self, doc_id: str, keep_ids: list[str], refresh: bool = True) -> None:
        """다시 색인한 문서에서 이번에 만들지 않은 청크(사라진 섹션)를 지운다."""
        body = {"query": {"bool": {"filter": [{"term": {"doc_id": doc_id}}],
                                   "must_not": [{"ids": {"values": keep_ids}}]}}}
        self.client.delete_by_query(index=self.alias, body=body, refresh=refresh)

    def knn_search(self, vector: list[float], principals: list[str], k: int, *, space: str | None = None,
                   updated_after: datetime | None = None) -> list[dict[str, Any]]:
        """권한 필터를 knn 절 안에 넣은 벡터 검색. 볼 수 있는 청크가 k개 이상이면 k개를 돌려준다."""
        body = {
            "size": k,
            "_source": {"excludes": _SOURCE_EXCLUDES},
            "query": {"knn": {"embedding": {
                "vector": vector,
                "k": k,
                "filter": search_filter(principals, space=space, updated_after=updated_after),
            }}},
        }
        res = self.client.search(index=self.alias, body=body)
        return [{**h["_source"], "score": h["_score"]} for h in res["hits"]["hits"]]

    def recent_changes(self, principals: list[str], since: datetime, *, space: str | None = None,
                       size: int = 20) -> list[dict[str, Any]]:
        """since 이후 수정된 문서를 최신순으로 문서당 하나씩 돌려준다. 벡터 검색이 아니라 bool 필터로 충분하다."""
        body = {
            "size": size,
            "_source": {"excludes": _SOURCE_EXCLUDES + ["text"]},
            "query": search_filter(principals, space=space, updated_after=since),
            "collapse": {"field": "doc_id"},
            "sort": [{"updated_at": "desc"}],
        }
        res = self.client.search(index=self.alias, body=body)
        return [h["_source"] for h in res["hits"]["hits"]]
