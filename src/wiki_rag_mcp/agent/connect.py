"""운영 검색 서버에 붙는 두 방법. 사람이 쓸 때는 브라우저 로그인(OAuth), 평가 스크립트는 미리 받은 토큰.

데모 에이전트는 사용자 기기에서 도는 우리 클라이언트라 미리 등록한 공개 클라이언트 wiki-rag-demo로 로그인한다
(ADR-24). 돌아갈 주소는 127.0.0.1의 빈 포트다. 인가 서버는 루프백 주소의 포트를 비교하지 않는다(RFC 8252 7.3).
토큰은 메모리에만 두고, 프로그램이 끝나면 사라진다.
"""

import sys
import time
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

DEMO_CLIENT_ID = "wiki-rag-demo"
LOGIN_TIMEOUT = 300  # 브라우저에서 로그인을 기다리는 시간(초)


@asynccontextmanager
async def connect(url: str, auth: httpx2.Auth) -> AsyncIterator[Client]:
    # MCP SDK가 권하는 시간 제한과 같다: 연결 30초, 서버가 응답 흐름을 오래 열어 둘 수 있어 읽기는 300초
    timeout = httpx2.Timeout(30, read=300)
    async with httpx2.AsyncClient(auth=auth, timeout=timeout) as http, \
            Client(streamable_http_client(url, http_client=http)) as client:
        yield client


class BearerToken(httpx2.Auth):
    """평가용. 토큰은 바깥에서 바꿔 끼운다(10분짜리라 사용자마다, 오래 걸리면 다시 받는다)."""

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class _MemoryStorage:
    """SDK의 TokenStorage. 클라이언트 정보는 미리 등록한 값이라 처음부터 채워 둔다(동적 등록을 하지 않게)."""

    def __init__(self, client_info: OAuthClientInformationFull):
        self.client_info = client_info
        self.tokens: OAuthToken | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != "/callback":
            self.send_error(404)
            return
        self.server.params = {k: v[0] for k, v in parse_qs(url.query).items()}  # type: ignore[attr-defined]
        body = "로그인이 끝났습니다. 이 창을 닫고 터미널로 돌아가세요.".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # 요청마다 표준 오류에 찍지 않는다
        pass


class LoopbackReceiver:
    """인가 서버가 브라우저를 돌려보내는 127.0.0.1 주소에서 인가 응답을 한 번 받는다."""

    def __init__(self):
        self._server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._server.params = None  # type: ignore[attr-defined]
        self._server.timeout = 1
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_port}/callback"

    def close(self) -> None:
        self._server.server_close()

    async def wait(self) -> AuthorizationCodeResult:
        params = await anyio.to_thread.run_sync(self._serve)
        if "code" not in params:
            raise SystemExit(f"로그인하지 못했습니다: {params.get('error_description') or params.get('error')}")
        return AuthorizationCodeResult(code=params["code"], state=params.get("state"), iss=params.get("iss"))

    def _serve(self) -> dict[str, str]:
        deadline = time.monotonic() + LOGIN_TIMEOUT
        while self._server.params is None:  # type: ignore[attr-defined]
            if time.monotonic() > deadline:
                raise SystemExit(f"{LOGIN_TIMEOUT}초 안에 로그인하지 않았습니다.")
            self._server.handle_request()
        return self._server.params  # type: ignore[attr-defined]


async def _open_browser(url: str) -> None:
    print(f"브라우저에서 로그인하세요. 창이 열리지 않으면 이 주소를 여세요:\n{url}\n", file=sys.stderr)
    webbrowser.open(url)


def browser_login(url: str, receiver: LoopbackReceiver) -> OAuthClientProvider:
    """인가 코드 + PKCE. 탐색(RFC 9728, RFC 8414), 토큰 대상 지정(RFC 8707), 갱신은 SDK가 한다."""
    client = {"redirect_uris": [receiver.redirect_uri], "token_endpoint_auth_method": "none",
              "grant_types": ["authorization_code", "refresh_token"]}
    info = OAuthClientInformationFull.model_validate(client | {"client_id": DEMO_CLIENT_ID})
    metadata = OAuthClientMetadata.model_validate(client | {"client_name": DEMO_CLIENT_ID})
    return OAuthClientProvider(server_url=url, client_metadata=metadata, storage=_MemoryStorage(info),
                               redirect_handler=_open_browser, callback_handler=receiver.wait)
