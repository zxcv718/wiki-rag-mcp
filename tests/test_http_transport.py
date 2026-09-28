"""HTTP 전송(M5): OAuth 액세스 토큰으로 사용자와 클라이언트 등급을 정하는지 확인한다 (ADR-06, ADR-17, ADR-24).

토큰 형식과 검증 규칙은 wiki-service/README.md "인가 서버" 절이 계약이다. 가짜 인가 서버(tests/oauth.py)가
테스트 안에서 만든 RSA 키로 서명하고, 서버는 ASGI 앱으로 불러 네트워크 없이 돈다. 검색 저장소는 권한 규칙
(filters.allows)으로 거르는 메모리 저장소다. 실제 pre-filter SQL을 HTTP로 거치는 시험은 권한 테스트셋
(test_permissions_integration.py)이 한다.
"""

import time
from pathlib import Path

import anyio
import jwt
import pytest
from jwt import PyJWKClient
from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.oauth import (
    BASE_URL,
    CIMD_CLIENT,
    DEMO_AGENT,
    ISSUER,
    RESOURCE,
    FakeAuthServer,
    http_app,
    http_client,
    http_settings,
    mcp_session,
    serving,
)
from wiki_rag_mcp.auth.tokens import JwtVerifier
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.search.filters import allows
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.server.responses import CONFIDENTIAL_NOTE, NOT_FOUND
from wiki_rag_mcp.wiki.files import FileWikiSource

FIXTURE = Path(__file__).parent / "fixtures" / "wiki"
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                         "clientInfo": {"name": "test", "version": "0"}}}


class ZeroEmbedder:
    def encode_query(self, _text):
        return [0.0]


class PermissionStore:
    """권한 규칙으로 거르는 메모리 검색 저장소. 받은 principal 목록을 적어 둔다."""

    def __init__(self, docs):
        self.docs = docs
        self.principals: list[list[str]] = []

    def knn_search(self, _vector, principals, k, *, space=None, updated_after=None):
        self.principals.append(principals)
        return [{"doc_id": d.doc_id, "title": d.title, "section_path": [], "text": d.body, "url": d.url,
                 "version": d.version, "updated_at": d.updated_at.isoformat(), "score": 1.0,
                 "classification": str(d.classification)}
                for d in self.docs if allows(principals, d.space_principals, d.restricted_principals)][:k]


def services(**settings) -> Services:
    source = FileWikiSource(FIXTURE)
    return Services(http_settings(wiki_dir=FIXTURE, **settings), source, PermissionStore(source.documents()),
                    ZeroEmbedder())


def run_app(action, auth: FakeAuthServer | None = None, svc: Services | None = None):
    app = http_app(svc or services(), auth or FakeAuthServer())

    async def go():
        async with serving(app):
            return await action(app)

    return anyio.run(go)


async def post_initialize(app, token=None, **headers):
    async with http_client(app, token, **headers) as http:
        return await http.post("/mcp", json=INITIALIZE, headers={"Accept": "application/json, text/event-stream"})


def verify(verifier: JwtVerifier, token: str):
    return anyio.run(verifier.verify_token, token)


def test_request_without_token_gets_401_pointing_to_resource_metadata():
    response = run_app(post_initialize)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ") and 'error="invalid_token"' in challenge
    assert f'resource_metadata="{BASE_URL}/.well-known/oauth-protected-resource/mcp"' in challenge


def test_protected_resource_metadata_names_this_server_and_the_wiki_authorization_server():
    async def action(app):
        async with http_client(app) as http:
            return await http.get("/.well-known/oauth-protected-resource/mcp")

    response = run_app(action)
    assert response.status_code == 200
    body = response.json()
    assert (body["resource"], body["authorization_servers"], body["scopes_supported"]) == (
        RESOURCE, [ISSUER], ["wiki:read"])


def test_health_answers_without_a_token():
    async def action(app):
        async with http_client(app) as http:
            return await http.get("/health")

    response = run_app(action)
    assert (response.status_code, response.text) == (200, "OK")


@pytest.mark.parametrize("forge", [
    pytest.param(lambda a: a.token(key=FakeAuthServer().private_key), id="signed-by-another-key"),
    pytest.param(lambda a: FakeAuthServer().token(), id="unknown-kid"),
    pytest.param(lambda a: a.token(iss="https://auth.evil.example"), id="other-issuer"),
    pytest.param(lambda a: a.token(iss=ISSUER[:-1]), id="issuer-prefix"),
    pytest.param(lambda a: a.token(aud="https://other-mcp.example/mcp"), id="other-audience"),
    pytest.param(lambda a: a.token(aud=None), id="no-audience"),
    pytest.param(lambda a: a.token(exp=int(time.time()) - 120), id="expired"),
    pytest.param(lambda a: a.token(exp=None), id="no-expiry"),
    pytest.param(lambda a: a.unsigned_token(), id="alg-none"),
    pytest.param(lambda a: a.hs256_forgery(), id="hs256-with-public-key"),
    pytest.param(lambda a: a.token(client_id=None), id="no-client-id"),
    pytest.param(lambda a: a.token(sub="user:bob"), id="sub-with-prefix"),
    pytest.param(lambda a: a.token(sub="../bob"), id="sub-with-path"),
    pytest.param(lambda a: "not-a-jwt", id="garbage"),
])
def test_invalid_tokens_get_401(forge):
    auth = FakeAuthServer()
    response = run_app(lambda app: post_initialize(app, forge(auth)), auth)
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


@pytest.mark.parametrize("scope", ["wiki:read", "openid wiki:read", ["wiki:read"]])
def test_valid_token_is_accepted_with_scope_as_string_or_list(scope):
    auth = FakeAuthServer()
    assert run_app(lambda app: post_initialize(app, auth.token(scope=scope)), auth).status_code == 200


@pytest.mark.parametrize("scope", ["", "openid profile", None])
def test_token_without_wiki_read_gets_403(scope):
    auth = FakeAuthServer()
    response = run_app(lambda app: post_initialize(app, auth.token(scope=scope)), auth)
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]


def test_unknown_host_and_browser_origin_are_refused():
    """DNS 리바인딩 방어. Host는 resource 주소의 호스트만, Origin은 설정에 적은 것만 받는다."""
    auth = FakeAuthServer()

    async def action(app):
        token = auth.token()
        return [(await post_initialize(app, token, Host="evil.example")).status_code,
                (await post_initialize(app, token, Origin="https://evil.example")).status_code,
                (await post_initialize(app, token, Origin="https://claude.ai")).status_code,
                (await post_initialize(app, token)).status_code]

    assert run_app(action, auth, services(allowed_origins=("https://claude.ai",))) == [421, 403, 200, 200]


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_valid_token_calls_tools_in_old_and_new_protocols(mode):
    auth = FakeAuthServer()

    async def action(client):
        return await client.list_tools(), await client.call_tool("get_document", {"doc_id": "hr-003"})

    tools, doc = run_app(lambda app: mcp_session(app, auth.token("bob"), action, mode), auth)
    assert {t.name for t in tools.tools} == {"search_wiki", "get_document", "list_recent_changes"}
    assert not doc.is_error and doc.structured_content["doc_id"] == "hr-003"


def test_each_token_sees_only_its_own_users_documents_even_concurrently():
    """dana는 dba라 제한 문서 infra-002를 보고, bob은 못 본다. 두 사용자가 한 서버에 동시에 붙어도 섞이지 않는다."""
    auth = FakeAuthServer()
    svc = services()
    seen: dict[str, list] = {}

    async def calls(client):
        out = []
        for _ in range(3):
            found = await client.call_tool("search_wiki", {"query": "운영 DB 비밀번호", "top_k": 10})
            doc = await client.call_tool("get_document", {"doc_id": "infra-002"})
            try:
                resource = (await client.read_resource("wiki://doc/infra-002")).contents[0].text
            except MCPError as e:
                resource = e.error.message
            out.append(({r["doc_id"] for r in found.structured_content["results"]}, doc, resource))
            await anyio.sleep(0)
        return out

    async def action(app):
        async def as_user(user):
            seen[user] = await mcp_session(app, auth.token(user), calls)

        async with anyio.create_task_group() as tg:
            for user in ("dana", "bob", "dana", "bob"):
                tg.start_soon(as_user, user)

    run_app(action, auth, svc)
    for found, doc, resource in seen["dana"]:
        assert "infra-002" in found and "90일" in doc.structured_content["content"] and "90일" in resource
    for found, doc, resource in seen["bob"]:
        assert "infra-002" not in found
        assert doc.is_error and doc.content[0].text.endswith(NOT_FOUND) and NOT_FOUND in resource
    assert {p[0] for p in svc.store.principals} == {"user:dana", "user:bob"}
    assert all(("group:dba" in p) == (p[0] == "user:dana") for p in svc.store.principals)


@pytest.mark.parametrize(("client_id", "client_tier", "internal"), [
    pytest.param(DEMO_AGENT, "internal", True, id="internal"),
    pytest.param(CIMD_CLIENT, "external", False, id="external"),
    pytest.param(DEMO_AGENT, None, False, id="no-claim-with-a-once-internal-client-id"),
    pytest.param(DEMO_AGENT, "INTERNAL", False, id="uppercase"),
    pytest.param(DEMO_AGENT, "admin", False, id="unknown-value"),
    pytest.param(DEMO_AGENT, ["internal"], False, id="not-a-string"),
])
def test_confidential_content_depends_only_on_the_client_tier_claim(client_id, client_tier, internal):
    """ADR-17 표: 기밀 문서는 사내 클라이언트에만 스니펫·본문을 주고, 외부 클라이언트에는 제목과 링크만 준다.

    등급은 인가 서버가 서명해 넣은 client_tier가 정확히 internal일 때만 사내다. client_id는 보지 않는다. 공개
    클라이언트의 id는 비밀이 아니어서 다른 앱도 같은 id로 토큰을 받을 수 있기 때문이다(ADR-24).
    """
    auth = FakeAuthServer()

    async def action(client):
        found = await client.call_tool("search_wiki", {"query": "연봉 조정", "top_k": 10})
        doc = await client.call_tool("get_document", {"doc_id": "hr-int-001"})
        resource = await client.read_resource("wiki://doc/hr-int-001")
        return found, doc, resource

    token = auth.token("hana", client_id, client_tier=client_tier)
    found, doc, resource = run_app(lambda app: mcp_session(app, token, action), auth)
    hit = next(r for r in found.structured_content["results"] if r["doc_id"] == "hr-int-001")
    body = doc.structured_content
    assert body["title"] and body["url"]
    assert bool(hit["snippet"]) == bool(body["content"]) == ("8%" in resource.contents[0].text) == internal
    assert (CONFIDENTIAL_NOTE in body["notes"]) == (CONFIDENTIAL_NOTE in hit["notes"]) == (not internal)


def test_tools_without_a_verified_token_fail_instead_of_falling_back():
    """HTTP 모드에서 토큰 없이 도구가 불리면 오류다. 실행 환경의 사용자나 기본 등급으로 떨어지지 않는다."""
    svc = services()

    async def go():
        async with Client(build_server(svc, FakeAuthServer().verifier())) as client:
            return await client.call_tool("get_document", {"doc_id": "company-001"})

    result = anyio.run(go)
    assert result.is_error and "인증된 사용자가 없습니다" in result.content[0].text


def test_signing_keys_are_cached_and_a_new_kid_is_fetched_again():
    auth = FakeAuthServer()
    verifier = auth.verifier()
    assert verify(verifier, auth.token("bob")) and verify(verifier, auth.token("dana"))
    assert auth.fetches == 1
    auth.rotate()  # 인가 서버가 새 키로 다시 떴다
    token = verify(verifier, auth.token("bob", DEMO_AGENT, client_tier="internal"))
    assert (token.subject, token.client_id, token.scopes, token.resource) == ("bob", DEMO_AGENT, ["wiki:read"],
                                                                               RESOURCE)
    assert token.claims["client_tier"] == "internal"
    assert auth.fetches == 2


def test_small_clock_skew_is_tolerated():
    auth = FakeAuthServer()
    now = int(time.time())
    assert verify(auth.verifier(), auth.token(exp=now - 10))
    assert verify(auth.verifier(), auth.token(iat=now + 10))


@pytest.mark.parametrize("error", [jwt.PyJWKClientConnectionError("응답 없음"), ValueError("JSON이 아님")])
def test_jwks_failure_rejects_the_token_instead_of_crashing(error):
    """검증기에서 예외가 새면 SDK가 401이 아니라 서버 오류로 답한다."""

    class Down(PyJWKClient):
        def fetch_data(self):
            raise error

    assert verify(JwtVerifier(ISSUER, RESOURCE, Down(f"{ISSUER}/oauth2/jwks")), FakeAuthServer().token()) is None


def test_jwks_address_defaults_to_the_contract_path():
    assert JwtVerifier.from_settings(http_settings()).keys.uri == f"{ISSUER}/oauth2/jwks"
    custom = http_settings(jwks_url="http://wiki:8081/oauth2/jwks")
    assert JwtVerifier.from_settings(custom).keys.uri == "http://wiki:8081/oauth2/jwks"


def test_http_mode_refuses_stdio_identity_settings():
    """HTTP에서는 사용자와 등급을 토큰으로만 정한다. WIKI_USER를 조용히 무시하지 않고 시작부터 막는다."""
    for bad in [{"user": "bob"}, {"client_tier": "internal"}, {"resource_url": "https://mcp.example"}]:
        with pytest.raises(ValueError):
            Settings(transport="http", **bad)
    with pytest.raises(ValueError):
        Settings(transport="sse")


def test_http_settings_from_env(monkeypatch):
    for name in ["WIKI_USER", "WIKI_CLIENT_TIER", "WIKI_MCP_ALLOWED_ORIGINS", "WIKI_AUTH_JWKS_URL",
                 "WIKI_MCP_INTERNAL_CLIENTS"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WIKI_MCP_TRANSPORT", "http")
    monkeypatch.setenv("WIKI_MCP_RESOURCE_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("WIKI_AUTH_ISSUER", "https://auth.example")
    monkeypatch.setenv("WIKI_MCP_ALLOWED_HOSTS", "mcp.example, localhost:8000,")
    settings = Settings.from_env()
    assert (settings.allowed_hosts, settings.allowed_origins) == (("mcp.example", "localhost:8000"), ())
    assert (settings.resource_url, settings.auth_issuer) == ("https://mcp.example/mcp", "https://auth.example")
    monkeypatch.setenv("WIKI_USER", "bob")
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.parametrize("value", ["demo-agent, load-test", ""])
def test_old_internal_client_list_stops_startup(monkeypatch, value):
    """예전 설정이 남아 있으면 조용히 무시하지 않는다. 등급이 인가 서버로 옮겨 간 것을 운영자가 알아야 한다."""
    monkeypatch.setenv("WIKI_MCP_INTERNAL_CLIENTS", value)
    with pytest.raises(ValueError, match="client_tier"):
        Settings.from_env()
