"""색인 파이프라인을 가짜 인코더로 확인한다. 진짜 모델은 `wiki-rag-index`로 따로 돌린다."""

import hashlib
import uuid
from pathlib import Path

import numpy as np
import pytest
from opensearchpy import OpenSearch

from wiki_rag_mcp.indexing.indexer import index_all
from wiki_rag_mcp.search.store import OpenSearchStore
from wiki_rag_mcp.wiki.files import FileWikiSource

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parent / "fixtures" / "wiki"
DIM = 8


class FakeEncoder:
    model_name, revision, dtype = "fake", "0", "float32"

    def encode_documents(self, texts):
        vecs = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:DIM], dtype=np.uint8).astype(np.float32) + 1
                for t in texts]
        return np.array([v / np.linalg.norm(v) for v in vecs])


def words(text):
    return len(text.split())


@pytest.fixture
def store():
    client = OpenSearch(hosts=["http://127.0.0.1:9200"], timeout=30)
    try:
        client.info()
    except Exception:
        pytest.skip("로컬 OpenSearch가 떠 있지 않다")
    s = OpenSearchStore(client, f"test-index-{uuid.uuid4().hex[:8]}")
    s.ensure_index(dim=DIM)
    yield s
    s.drop()


def count(store, **term):
    body = {"query": {"term": term}} if term else {"query": {"match_all": {}}}
    return store.client.count(index=store.alias, body=body)["count"]


def test_indexes_every_document_with_permission_fields(store):
    stats = index_all(FileWikiSource(FIXTURE), store, FakeEncoder(), words)
    assert stats.documents == 11
    assert count(store) == stats.chunks
    hit = store.client.search(index=store.alias, body={"query": {"term": {"doc_id": "infra-002"}}})["hits"]["hits"][0]
    src = hit["_source"]
    assert src["restricted_principals"] == ["group:dba"]
    assert src["space_principals"] == ["group:infra", "group:eng"]
    assert src["embedding_model"] == "fake"
    assert "운영 DB" in src["text"] and not src["text"].startswith("인프라 >")  # 맥락 헤더는 저장하지 않는다


def test_reindex_removes_chunks_of_deleted_sections(store, tmp_path):
    import shutil

    wiki = tmp_path / "wiki"
    shutil.copytree(FIXTURE, wiki)
    index_all(FileWikiSource(wiki), store, FakeEncoder(), words)
    before = count(store, doc_id="eng-001")
    doc = wiki / "docs" / "eng-001.md"
    doc.write_text(doc.read_text(encoding="utf-8").split("## 재발 방지")[0], encoding="utf-8")
    index_all(FileWikiSource(wiki), store, FakeEncoder(), words)
    assert count(store, doc_id="eng-001") == before - 1
