"""권한·등급 테스트셋 (4장 "검증 방법"). 생성한 가상 위키(data/wiki) 전체를 색인하고, 가상 사용자 전원에 대해
도구 3개와 문서 리소스 어디에도 권한 밖 문서가 나오지 않는지, 기밀 문서의 내용이 외부 클라이언트로 나가지
않는지 확인한다.

정답(누가 무엇을 볼 수 있는가)은 서버 코드가 아니라 생성 스키마로 따로 계산한다(tools/wikigen/spec.py).
서버의 권한 규칙과 독립된 구현이 같은 답을 내야 통과한다. 검색 품질이 아니라 누출을 보는 시험이라
임베딩은 가짜 인코더를 쓴다. 가까운 문서가 무엇이든 권한 밖 문서가 나오면 안 되기 때문이다.
"""

import hashlib
import json
import uuid
from pathlib import Path

import anyio
import numpy as np
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from opensearchpy import OpenSearch

from tools.wikigen.spec import load_spec
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.indexer import index_all
from wiki_rag_mcp.search.store import OpenSearchStore
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.server.responses import NOT_FOUND
from wiki_rag_mcp.wiki.files import FileWikiSource

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parent.parent
WIKI = ROOT / "data" / "wiki"
DIM = 16


class FakeEncoder:
    model_name, revision, dtype = "fake", "0", "float32"

    @staticmethod
    def _vec(text: str) -> np.ndarray:
        raw = np.frombuffer(hashlib.sha256(text.encode()).digest()[:DIM], dtype=np.uint8).astype(np.float32) + 1
        return raw / np.linalg.norm(raw)

    def encode_documents(self, texts):
        return np.array([self._vec(t) for t in texts])

    def encode_query(self, text):
        return self._vec(text)


@pytest.fixture(scope="module")
def wiki():
    if not any((WIKI / "docs").glob("*.md")):
        pytest.skip("가상 위키가 아직 생성되지 않았다 (python -m tools.wikigen)")
    client = OpenSearch(hosts=["http://127.0.0.1:9200"], timeout=60)
    try:
        client.info()
    except Exception:
        pytest.skip("로컬 OpenSearch가 떠 있지 않다")
    source = FileWikiSource(WIKI)
    store = OpenSearchStore(client, f"test-perm-{uuid.uuid4().hex[:8]}")
    store.ensure_index(dim=DIM)
    index_all(source, store, FakeEncoder(), lambda text: len(text.split()))
    spec = load_spec(WIKI / "schema.yaml", ROOT / "tools" / "wikigen" / "plants.yaml")
    yield spec, source, store
    store.drop()


def visible(spec, source, user) -> set[str]:
    """생성 스키마로 계산한 정답. 서버의 filters.allows를 쓰지 않는다."""
    return {d.doc_id for d in source.documents() if spec.can_see(user, d.space_principals, d.restricted_principals)}


def session(source, store, user, tier, action):
    services = Services(Settings(wiki_dir=WIKI, user=user, client_tier=tier), source, store, FakeEncoder())

    async def go():
        async with Client(build_server(services)) as client:
            return await action(client)

    return anyio.run(go)


def test_every_virtual_user_sees_something_and_not_everything(wiki):
    spec, source, _ = wiki
    everything = {d.doc_id for d in source.documents()}
    for user in spec.users:
        assert 0 < len(visible(spec, source, user)) < len(everything), user


@pytest.mark.parametrize("tier", ["external", "internal"])
def test_no_tool_or_resource_leaks_documents(wiki, tier):
    spec, source, store = wiki
    docs = source.documents()
    confidential = {d.doc_id for d in docs if d.classification == "confidential"}
    # 민감한 문서(제한, 기밀, 빈 권한, 기밀 스페이스)의 제목을 질의로 쓴다. 가짜 인코더라 질의마다 다른 방향을 찌른다
    sensitive = [d.title for d in docs if d.restricted_principals != ("all",) or d.doc_id in confidential
                 or d.space_principals != ("all",)][:80]

    for user in spec.users:
        allowed = visible(spec, source, user)

        async def action(client, allowed=allowed):
            leaks = []
            for query in sensitive:
                res = await client.call_tool("search_wiki", {"query": query, "top_k": 10})
                for r in res.structured_content["results"]:
                    if r["doc_id"] not in allowed:
                        leaks.append(("search_wiki", r["doc_id"]))
                    if tier == "external" and r["doc_id"] in confidential and r["snippet"]:
                        leaks.append(("search_wiki 기밀 스니펫", r["doc_id"]))
            listed = set()
            for space in spec.spaces:
                res = await client.call_tool("list_recent_changes", {"since": "2000-01-01", "space": space,
                                                                     "limit": 50})
                listed |= {r["doc_id"] for r in res.structured_content["results"]}
            leaks += [("list_recent_changes", i) for i in listed - allowed]
            missing = allowed - listed
            for d in docs:
                res = await client.call_tool("get_document", {"doc_id": d.doc_id})
                if d.doc_id in allowed:
                    body = res.structured_content["content"]
                    if tier == "external" and d.doc_id in confidential and body:
                        leaks.append(("get_document 기밀 본문", d.doc_id))
                elif not (res.is_error and res.content[0].text.endswith(NOT_FOUND)):
                    leaks.append(("get_document", d.doc_id))
                try:
                    content = json.loads((await client.read_resource(f"wiki://doc/{d.doc_id}")).contents[0].text)
                    if d.doc_id not in allowed:
                        leaks.append(("resource", d.doc_id))
                    elif tier == "external" and d.doc_id in confidential and content["content"]:
                        leaks.append(("resource 기밀 본문", d.doc_id))
                except MCPError as e:
                    if d.doc_id in allowed or NOT_FOUND not in e.error.message:
                        leaks.append(("resource 오류", d.doc_id))
            return leaks, missing

        leaks, missing = session(source, store, user, tier, action)
        assert leaks == [], (user, leaks[:10])
        assert missing == set(), (user, "볼 수 있는데 변경 목록에 없는 문서", sorted(missing)[:10])


def test_restricted_users_still_get_k_results(wiki):
    """권한 밖 문서가 가까워도 볼 수 있는 청크가 k개 이상이면 k개를 받는다 (4장 "결과 수")."""
    spec, source, store = wiki
    enc = FakeEncoder()
    for user in spec.users:
        principals = [f"user:{user}", *source.groups_of(user)]
        for title in [d.title for d in source.documents()][:20]:
            hits = store.knn_search(enc.encode_query(title), principals, 10)
            allowed_chunks = store.client.count(index=store.alias, body={"query": {"bool": {"filter": [
                {"terms": {"space_principals": sorted({*principals, "all"})}},
                {"terms": {"restricted_principals": sorted({*principals, "all"})}},
            ]}}})["count"]
            assert len(hits) == min(10, allowed_chunks), (user, title)


def test_keyword_and_hybrid_search_do_not_leak(wiki):
    """하이브리드 검색(ADR-12)도 두 검색 모두 권한 필터를 통과한 후보만 합치는지 본다. 채택되면 서버가 쓴다."""
    spec, source, store = wiki
    enc = FakeEncoder()
    titles = [d.title for d in source.documents()]
    for user in spec.users:
        allowed = visible(spec, source, user)
        principals = [f"user:{user}", *source.groups_of(user)]
        for title in titles:
            for hits in (store.bm25_search(title, principals, 10),
                         store.hybrid_search(enc.encode_query(title), title, principals, 10)):
                leaked = {h["doc_id"] for h in hits} - allowed
                assert not leaked, (user, title, leaked)
