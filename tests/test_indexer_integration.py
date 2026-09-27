"""색인 파이프라인과, 색인된 권한 필드로 거르는 list_recent_changes를 가짜 인코더로 확인한다.

진짜 모델은 `wiki-rag-index`로 따로 돌린다.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from tests.pg import count, new_store
from wiki_rag_mcp.indexing.indexer import index_all
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
    s = new_store("index", DIM)
    yield s
    s.drop()


def test_indexes_every_document_with_permission_fields(store):
    stats = index_all(FileWikiSource(FIXTURE), store, FakeEncoder(), words)
    assert stats.documents == 11
    assert count(store) == stats.chunks
    src = store.conn.execute(
        f"SELECT restricted_principals, space_principals, embedding_model, text FROM {store.table} "
        "WHERE doc_id = 'infra-002' LIMIT 1").fetchone()
    src = dict(zip(["restricted_principals", "space_principals", "embedding_model", "text"], src, strict=True))
    assert src["restricted_principals"] == ["group:dba"]
    assert src["space_principals"] == ["group:infra", "group:eng"]
    assert src["embedding_model"] == "fake"
    assert "운영 DB" in src["text"] and not src["text"].startswith("인프라 >")  # 맥락 헤더는 저장하지 않는다


def test_reindex_removes_chunks_of_deleted_sections(store, tmp_path):
    import shutil

    wiki = tmp_path / "wiki"
    shutil.copytree(FIXTURE, wiki)
    index_all(FileWikiSource(wiki), store, FakeEncoder(), words)
    before = count(store, "doc_id = %s", ("eng-001",))
    doc = wiki / "docs" / "eng-001.md"
    doc.write_text(doc.read_text(encoding="utf-8").split("## 재발 방지")[0], encoding="utf-8")
    index_all(FileWikiSource(wiki), store, FakeEncoder(), words)
    assert count(store, "doc_id = %s", ("eng-001",)) == before - 1


@pytest.mark.parametrize("user", ["bob", "dana", "erin", "hana", "kim"])
def test_recent_changes_tool_matches_the_wiki_permission_rule(store, user):
    """list_recent_changes(인덱스 필터)와 위키의 본문 재확인 규칙(allows)이 같은 문서를 보여야 한다."""
    import anyio
    from mcp import Client

    from wiki_rag_mcp.config import Settings
    from wiki_rag_mcp.search.filters import allows
    from wiki_rag_mcp.server.app import Services, build_server

    source = FileWikiSource(FIXTURE)
    index_all(source, store, FakeEncoder(), words)
    since = datetime(2026, 1, 1, tzinfo=UTC)
    principals = [f"user:{user}", *source.groups_of(user)]
    expected = sorted((d for d in source.documents() if d.updated_at >= since
                       and allows(principals, d.space_principals, d.restricted_principals)),
                      key=lambda d: d.updated_at, reverse=True)
    services = Services(Settings(wiki_dir=FIXTURE, user=user), source, store, None)

    async def call():
        async with Client(build_server(services)) as client:
            return await client.call_tool("list_recent_changes", {"since": "2026-01-01"})

    result = anyio.run(call)
    assert not result.is_error, result.content
    got = [r["doc_id"] for r in result.structured_content["results"]]
    assert got == [d.doc_id for d in expected]
    assert "misc-001" not in got  # 권한 필드가 빈 문서
