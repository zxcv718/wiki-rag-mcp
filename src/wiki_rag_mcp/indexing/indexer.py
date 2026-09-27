"""위키 문서 전체를 청크로 나누고 임베딩해 검색 인덱스에 넣는다.

처음 색인하거나 평가용 인덱스를 새로 만들 때 쓴다. 문서가 바뀔 때마다 바뀐 섹션만 다시 임베딩하는 증분 색인은
incremental.py에 있다(ADR-09, 10, 19).
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from wiki_rag_mcp.indexing.chunker import CountTokens, chunk_document, context_header
from wiki_rag_mcp.models import Chunk, Document
from wiki_rag_mcp.search.backend import SearchStore
from wiki_rag_mcp.wiki.source import DocumentSource


class DocumentEncoder(Protocol):
    model_name: str
    revision: str
    dtype: str

    def encode_documents(self, texts: list[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class IndexStats:
    documents: int
    chunks: int
    seconds: float


def embedding_input(doc: Document, chunk: Chunk, space_title: str, with_header: bool = True) -> str:
    """맥락 헤더를 붙인 임베딩 입력. 헤더는 임베딩에만 쓰고 저장하는 본문에는 넣지 않는다.

    with_header=False는 헤더의 효과를 재는 판정 실험(7장 "맥락 헤더 제거")에만 쓴다.
    """
    if not with_header:
        return chunk.text
    return context_header(space_title, doc.title, chunk.section_path) + "\n" + chunk.text


def to_index_doc(doc: Document, chunk: Chunk, vector: np.ndarray, encoder: DocumentEncoder) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": doc.doc_id,
        "chunk_index": chunk.chunk_index,
        "space": doc.space,
        "title": doc.title,
        "section_path": list(chunk.section_path),
        "text": chunk.text,
        "url": doc.url,
        "version": doc.version,
        "revision": doc.revision,
        "updated_at": doc.updated_at.isoformat(),
        "section_hash": chunk.section_hash,
        "space_principals": list(doc.space_principals),
        "restricted_principals": list(doc.restricted_principals),
        "classification": str(doc.classification),
        "embedding": vector.tolist(),
        "embedding_model": encoder.model_name,
        "embedding_revision": encoder.revision,
        "embedding_dtype": encoder.dtype,
    }


def index_all(source: DocumentSource, store: SearchStore, encoder: DocumentEncoder, count: CountTokens,
              with_header: bool = True) -> IndexStats:
    """모든 문서를 다시 임베딩한다. 문서마다 청크 교체와 상태 기록을 한 트랜잭션으로 해서, 이후 이벤트가
    이 색인보다 오래된 변경을 다시 반영하지 않게 한다 (ADR-19)."""
    started = time.perf_counter()
    docs = source.documents()
    pairs = [(doc, chunk) for doc in docs for chunk in chunk_document(doc.doc_id, doc.body, doc.title, count)]
    inputs = [embedding_input(d, c, source.space_title(d.space), with_header) for d, c in pairs]
    vectors = encoder.encode_documents(inputs)
    rows: dict[str, list[dict[str, Any]]] = {doc.doc_id: [] for doc in docs}
    for (d, c), v in zip(pairs, vectors, strict=True):
        rows[d.doc_id].append(to_index_doc(d, c, v, encoder))
    for doc in docs:
        store.replace_document(doc.doc_id, rows[doc.doc_id], doc.revision)
    return IndexStats(documents=len(docs), chunks=len(pairs), seconds=time.perf_counter() - started)
