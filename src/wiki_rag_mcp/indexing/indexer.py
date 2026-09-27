"""위키 문서를 청크로 나누고 임베딩해 검색 인덱스에 넣는다.

M1은 전체 색인만 한다. 이벤트를 받아 바뀐 섹션만 다시 임베딩하는 증분 색인은 M3에서 붙인다(ADR-09, 10, 19).
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from wiki_rag_mcp.indexing.chunker import CountTokens, chunk_document, context_header
from wiki_rag_mcp.models import Chunk, Document
from wiki_rag_mcp.search.store import OpenSearchStore
from wiki_rag_mcp.wiki.source import WikiSource


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


def index_all(source: WikiSource, store: OpenSearchStore, encoder: DocumentEncoder, count: CountTokens,
              with_header: bool = True) -> IndexStats:
    started = time.perf_counter()
    docs = source.documents()
    pairs = [(doc, chunk) for doc in docs for chunk in chunk_document(doc.doc_id, doc.body, doc.title, count)]
    inputs = [embedding_input(d, c, source.space_title(d.space), with_header) for d, c in pairs]
    vectors = encoder.encode_documents(inputs)
    store.index_chunks([to_index_doc(d, c, v, encoder) for (d, c), v in zip(pairs, vectors, strict=True)])
    for doc in docs:
        store.delete_stale_chunks(doc.doc_id, [c.chunk_id for d, c in pairs if d.doc_id == doc.doc_id])
    return IndexStats(documents=len(docs), chunks=len(pairs), seconds=time.perf_counter() - started)
