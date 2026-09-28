"""권한·등급 테스트셋 (4장 "검증 방법"). 가상 위키 전체를 색인하고, 가상 사용자 전원에 대해 도구 3개와 문서
리소스 어디에도 권한 밖 문서가 나오지 않는지, 기밀 문서의 내용이 외부 클라이언트로 나가지 않는지 확인한다.

같은 시험을 두 위키로 돌린다.
- file: data/wiki 파일을 직접 읽는다.
- wiki: 가상 위키를 옮긴 Spring 위키 서비스(wiki-rag-seed)를 읽는다. 문서는 위키 API로 색인하고, 그룹은 Redis
  캐시를 거쳐 위키에서 해석하고, 본문은 위키가 권한을 다시 판단해 준다. 서버가 실제로 쓰는 경로다(M4).

도구 3개와 리소스의 누출 시험은 전송도 둘로 돌린다. stdio는 실행 환경의 사용자와 등급을 쓰고, http는 가짜
인가 서버가 서명한 액세스 토큰의 sub와 client_tier 클레임으로 정한다(M5, tests/oauth.py).

정답(누가 무엇을 볼 수 있는가)은 서버 코드가 아니라 생성 기록과 스키마로 따로 계산한다(tools/wikigen/spec.py의
ledger, can_see). 문서의 권한 필드도 문서를 읽는 코드(파일 위키 파서, 위키 서비스)가 아니라 문서를 만든 계획에서
가져온다. 서버의 권한 규칙과 독립된 구현이 같은 답을 내야 통과한다. 검색 품질이 아니라 누출을 보는 시험이라
임베딩은 가짜 인코더를 쓴다. 가까운 문서가 무엇이든 권한 밖 문서가 나오면 안 되기 때문이다.
"""

import json
from pathlib import Path

import anyio
import numpy as np
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from psycopg import sql

from tests.fakeencoder import DIM, FakeEncoder, count_words
from tests.oauth import CIMD_CLIENT, DEMO_AGENT, FakeAuthServer, http_app, http_settings, mcp_session, serving
from tests.pg import count, new_store
from tests.services import redis_client, unavailable, wiki_source
from tools.wikigen.spec import ledger, load_spec
from wiki_rag_mcp.auth.groups import GroupCache
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.indexer import index_all
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.server.responses import NOT_FOUND
from wiki_rag_mcp.wiki.files import FileWikiSource

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parent.parent
WIKI = ROOT / "data" / "wiki"


@pytest.fixture(scope="module", params=["file", "wiki"])
def wiki(request):
    if not any((WIKI / "docs").glob("*.md")):
        unavailable("가상 위키가 아직 생성되지 않았다 (python -m tools.wikigen)")
    spec = load_spec(WIKI / "schema.yaml", ROOT / "tools" / "wikigen" / "plants.yaml")
    planned = ledger(spec, WIKI)
    if request.param == "file":
        source = groups = FileWikiSource(WIKI)
        client = None
    else:
        source = wiki_source()
        client = redis_client()
        groups = GroupCache(source, client)
        # 정답은 생성 스키마의 멤버십으로 계산하므로, 위키의 멤버십이 시드 때와 같아야 시험이 성립한다
        differs = [u for u, g in spec.users.items() if sorted(source.groups_of(u)) != sorted(f"group:{x}" for x in g)]
        if differs:
            unavailable(f"위키의 멤버십이 가상 위키와 다르다: {differs[:5]} (빈 위키에 wiki-rag-seed)")
    docs = source.documents()
    extra = {d.doc_id for d in docs} ^ set(planned)
    if extra:
        unavailable(f"위키의 문서가 가상 위키와 다르다: {sorted(extra)[:5]} (빈 위키에 wiki-rag-seed)")
    store = new_store(f"perm_{request.param}", DIM)
    index_all(source, store, FakeEncoder(), count_words)
    yield spec, Wiki(source, groups, docs, planned), store
    store.drop()
    if client is not None:
        client.close()


class Wiki:
    """시험 대상 위키. 문서 목록은 색인할 때 한 번 읽어 둔다."""

    def __init__(self, source, groups, docs, planned):
        self.source, self.groups, self.docs, self.planned = source, groups, docs, planned

    def documents(self):
        return self.docs

    def confidential(self) -> set[str]:
        return {i for i, p in self.planned.items() if p.classification == "confidential"}


def visible(spec, wiki, user) -> set[str]:
    """생성 기록으로 계산한 정답. 서버의 filters.allows도, 문서를 읽은 권한 필드도 쓰지 않는다."""
    return {i for i, p in wiki.planned.items() if spec.can_see(user, p.space_principals, p.restricted_principals)}


def test_indexed_permissions_match_the_generation_ledger(wiki):
    """색인한 문서의 권한과 등급이 문서를 만든 계획과 같다. 파일 파서나 위키로 옮기는 과정의 버그를 잡는다."""
    _, source, _ = wiki
    for d in source.documents():
        p = source.planned[d.doc_id]
        # 권한 목록은 집합이다. 위키 서비스는 정렬해서 돌려준다
        assert (sorted(d.space_principals), sorted(d.restricted_principals), str(d.classification)) == (
            sorted(p.space_principals), sorted(p.restricted_principals), p.classification), d.doc_id


def session(wiki, store, user, tier, action, transport="stdio"):
    if transport == "http":
        services = Services(http_settings(wiki_dir=WIKI), wiki.source, store, FakeEncoder(), wiki.groups)
        auth = FakeAuthServer()
        # 사내 등급은 인가 서버가 기밀 클라이언트에 넣어 주는 client_tier 클레임으로만 받는다 (ADR-24)
        token = auth.token(user, DEMO_AGENT if tier == "internal" else CIMD_CLIENT, client_tier=tier)

        async def go():
            async with serving(http_app(services, auth)) as app:
                return await mcp_session(app, token, action)

        return anyio.run(go)

    services = Services(Settings(wiki_dir=WIKI, user=user, client_tier=tier), wiki.source, store, FakeEncoder(),
                        wiki.groups)

    async def go():
        async with Client(build_server(services)) as client:
            return await action(client)

    return anyio.run(go)


def test_every_virtual_user_sees_something_and_not_everything(wiki):
    spec, source, _ = wiki
    everything = {d.doc_id for d in source.documents()}
    for user in spec.users:
        assert 0 < len(visible(spec, source, user)) < len(everything), user


def sensitive_queries(docs, limit=80):
    """민감한 문서(제한, 기밀, 빈 권한, 기밀 스페이스)의 제목. 가짜 인코더라 질의마다 다른 방향을 찌른다."""
    return [d.title for d in docs if d.restricted_principals != ("all",) or d.classification == "confidential"
            or d.space_principals != ("all",)][:limit]


def unfiltered_top(store, vector, k=10) -> list[str]:
    """필터를 끈 관리자 검색. 서버에는 이런 경로를 두지 않고 테스트에서만 저장소를 직접 조회한다."""
    query = sql.SQL("SELECT doc_id FROM {t} ORDER BY embedding <=> %s LIMIT %s").format(t=sql.Identifier(store.table))
    return [r[0] for r in store.conn.execute(query, [np.asarray(vector, dtype=np.float32), k]).fetchall()]


def test_filter_is_what_removes_forbidden_documents(wiki):
    """사용자 없이, 사용자로, 필터를 끄고 검색한 결과를 비교한다 (4장 "검증 방법").

    필터를 끄면 권한 밖 문서가 실제로 상위에 올라오는 질의에서, 사용자로 검색하면 그 문서가 사라져야 한다.
    필터를 꺼도 권한 밖 문서가 나오지 않는 질의만 있다면 누출 시험이 아무것도 증명하지 못한다.
    """
    spec, source, store = wiki
    enc = FakeEncoder()
    queries = sensitive_queries(source.documents())
    for user in spec.users:
        allowed = visible(spec, source, user)

        async def action(client, allowed=allowed, user=user):
            exposed_without_filter = 0
            for query in queries:
                if set(unfiltered_top(store, enc.encode_query(query))) - allowed:
                    exposed_without_filter += 1
                    res = await client.call_tool("search_wiki", {"query": query, "top_k": 10})
                    assert {r["doc_id"] for r in res.structured_content["results"]} <= allowed, (user, query)
            return exposed_without_filter

        assert session(source, store, user, "external", action) > 0, user

    async def anonymous(client):
        return [await client.call_tool(name, args) for name, args in [
            ("search_wiki", {"query": queries[0]}), ("list_recent_changes", {"since": "2000-01-01"}),
            ("get_document", {"doc_id": source.documents()[0].doc_id})]]

    assert all(r.is_error and "WIKI_USER" in r.content[0].text for r in session(source, store, None, "external",
                                                                                   anonymous))


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("tier", ["external", "internal"])
def test_no_tool_or_resource_leaks_documents(wiki, tier, transport):
    spec, source, store = wiki
    docs = source.documents()
    confidential = source.confidential()
    sensitive = sensitive_queries(docs)

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

        leaks, missing = session(source, store, user, tier, action, transport)
        assert leaks == [], (user, leaks[:10])
        assert missing == set(), (user, "볼 수 있는데 변경 목록에 없는 문서", sorted(missing)[:10])


def test_restricted_users_still_get_k_results(wiki):
    """권한 밖 문서가 가까워도 볼 수 있는 청크가 k개 이상이면 k개를 받는다 (4장 "결과 수")."""
    spec, source, store = wiki
    enc = FakeEncoder()
    for user in spec.users:
        principals = [f"user:{user}", *source.groups.groups_of(user)]
        for title in [d.title for d in source.documents()][:20]:
            hits = store.knn_search(enc.encode_query(title), principals, 10)
            mine = sorted({*principals, "all"})
            allowed_chunks = count(store, "space_principals && %s AND restricted_principals && %s", (mine, mine))
            assert len(hits) == min(10, allowed_chunks), (user, title)

