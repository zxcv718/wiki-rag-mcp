"""HTTP 전송 테스트가 쓰는 가짜 인가 서버와 MCP 클라이언트 도우미. 네트워크 없이 돈다.

테스트 안에서 RSA 키를 만들어 계약(wiki-service/README.md "인가 서버")대로 토큰에 서명한다. JWKS 조회는
PyJWKClient.fetch_data만 바꿔 끼워 메모리에서 돌려주므로, 키 캐시와 모르는 kid의 재조회는 실제 코드가 돈다.
서버는 ASGI 앱을 httpx2 ASGITransport로 부른다. MCP SDK 클라이언트가 쓰는 HTTP 라이브러리가 httpx2다.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from wiki_rag_mcp.auth.tokens import JwtVerifier
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.server.app import Services, build_server, http_options

# Settings의 로컬 기본값과 같다
ISSUER = Settings.auth_issuer
RESOURCE = Settings.resource_url
BASE_URL = "http://127.0.0.1:8000"
# 서버에서 도는 사전 등록 기밀 클라이언트. 인가 서버가 사내로 설정하면 토큰에 client_tier=internal을 넣는다
DEMO_AGENT = "wiki-demo-agent"
CIMD_CLIENT = "https://claude.ai/oauth/claude-code-client-metadata"  # CIMD 클라이언트는 id가 메타데이터 주소다


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


class FakeAuthServer:
    def __init__(self):
        self.fetches = 0
        self.rotate()

    def rotate(self) -> None:
        """서명 키를 바꾼다. 인가 서버를 키 파일 없이 다시 띄운 경우와 같다."""
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = uuid.uuid4().hex

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(self.private_key.public_key()))
        return {"keys": [jwk | {"kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def keys(self) -> PyJWKClient:
        server = self

        class Keys(PyJWKClient):
            def fetch_data(self):
                server.fetches += 1
                return server.jwks()

        return Keys(f"{ISSUER}/oauth2/jwks")

    def verifier(self) -> JwtVerifier:
        return JwtVerifier(ISSUER, RESOURCE, self.keys())

    def claims(self, sub: str, client_id: str, **overrides: Any) -> dict[str, Any]:
        """계약의 클레임. 등급은 기본으로 외부다. overrides에서 값이 None인 클레임은 뺀다."""
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": sub, "aud": RESOURCE, "client_id": client_id, "client_tier": "external",
                  "scope": "wiki:read", "iat": now, "exp": now + 600, "jti": uuid.uuid4().hex} | overrides
        return {k: v for k, v in claims.items() if v is not None}

    def token(self, sub: str = "bob", client_id: str = CIMD_CLIENT, *, key: Any = None, **overrides: Any) -> str:
        return jwt.encode(self.claims(sub, client_id, **overrides), key or self.private_key, algorithm="RS256",
                          headers={"kid": self.kid})

    def unsigned_token(self, sub: str = "bob") -> str:
        return jwt.encode(self.claims(sub, CIMD_CLIENT), None, algorithm="none", headers={"kid": self.kid})

    def hs256_forgery(self, sub: str = "bob") -> str:
        """공개 키 PEM을 HMAC 비밀로 쓴 위조 토큰. 헤더의 alg를 믿는 검증기는 공개 키로 이 서명을 맞다고 본다.

        PyJWT는 PEM을 HMAC 비밀로 쓰는 서명을 거부하므로 직접 만든다.
        """
        secret = self.private_key.public_key().public_bytes(serialization.Encoding.PEM,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        header = {"alg": "HS256", "typ": "JWT", "kid": self.kid}
        payload = self.claims(sub, CIMD_CLIENT)
        signing_input = _b64(json.dumps(header).encode()) + b"." + _b64(json.dumps(payload).encode())
        return (signing_input + b"." + _b64(hmac.new(secret, signing_input, hashlib.sha256).digest())).decode()


def http_settings(**overrides: Any) -> Settings:
    return Settings(transport="http", **overrides)


def http_app(services: Services, auth: FakeAuthServer):
    return build_server(services, auth.verifier()).streamable_http_app(**http_options(services.settings))


@asynccontextmanager
async def serving(app):
    """ASGITransport는 lifespan을 돌리지 않는다. SDK의 세션 관리자가 lifespan에서 작업 그룹을 연다."""
    async with app.router.lifespan_context(app):
        yield app


def http_client(app, token: str | None = None, **headers: str) -> httpx2.AsyncClient:
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url=BASE_URL, headers=auth | headers)


async def mcp_session(app, token: str, action, mode: str = "legacy"):
    """토큰 하나로 MCP 클라이언트를 열어 action(client)을 돌린다. legacy는 initialize로 시작하는 옛 방식이다."""
    async with (http_client(app, token) as http,
                Client(streamable_http_client(RESOURCE, http_client=http), mode=mode) as client):
        return await action(client)
