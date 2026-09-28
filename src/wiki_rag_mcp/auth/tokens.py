"""HTTP 전송의 액세스 토큰 검증 (ADR-06, ADR-24). 토큰 형식은 wiki-service/README.md "인가 서버" 절이 계약이다.

인가 서버가 RS256으로 서명한 JWT에서 서명, 발급자(iss), 대상(aud), 만료(exp), 사용자 id(sub) 형식을 확인한다.
클라이언트 신뢰 등급은 인가 서버가 정한 client_tier 클레임을 AccessToken.claims에 담아 요청마다 넘긴다(tier_of).
스코프(wiki:read)는 여기서 거르지 않고 SDK가 AuthSettings.required_scopes로 확인해 403 insufficient_scope로 답한다.
여기서 거르면 401이 되어, 클라이언트가 스코프 문제를 로그인 문제로 알고 다시 로그인만 반복한다.

- 알고리즘은 RS256으로 고정한다. 토큰 헤더의 alg를 따르면 alg: none이나, 공개 키를 HMAC 비밀로 쓴 HS256 위조를
  받게 된다(RFC 8725 2.1).
- 서명 키는 JWKS에서 kid로 찾는다. PyJWKClient가 키 묶음을 5분 캐시하고, 모르는 kid면 한 번 다시 가져온다.
  다시 가져오기는 30초에 한 번까지라, 아무 kid나 넣은 요청으로 인가 서버를 두드리게 할 수 없다.
- JWKS 조회는 표준 라이브러리 urllib의 막히는 호출이라 스레드에서 돌린다. 이벤트 루프가 멈추면 다른 사용자의
  요청까지 함께 멈춘다.
- 검증에 실패하면 이유와 관계없이 None을 돌려준다. SDK는 None을 401로 답하지만, 예외는 401이 아니라 서버 오류가 된다.
"""

import logging
from typing import Any

import anyio.to_thread
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken

from wiki_rag_mcp.auth.tiers import ClientTier
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.models import valid_user_id

log = logging.getLogger("wiki_rag_mcp.auth.tokens")

REQUIRED_SCOPE = "wiki:read"
ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 30  # 인가 서버와 시계가 조금 어긋나도 막 발급한 토큰을 거절하지 않게 한다
JWKS_CACHE_SECONDS = 300
JWKS_TIMEOUT_SECONDS = 3.0


def scopes_of(claims: dict[str, Any]) -> list[str]:
    """RFC 9068은 공백으로 구분한 문자열이고, Spring 인가 서버는 기본으로 배열을 낸다. 둘 다 받는다."""
    scope = claims.get("scope")
    if isinstance(scope, str):
        return scope.split()
    if isinstance(scope, list) and all(isinstance(s, str) for s in scope):
        return scope
    return []


def tier_of(token: AccessToken | None) -> ClientTier:
    """검증한 토큰의 client_tier 클레임. 정확히 internal일 때만 사내이고, 없거나 모르는 값이면 외부다.

    사내 등급은 인가 서버가 비밀로 인증한 기밀 클라이언트에만 준다. 공개 클라이언트의 id는 비밀이 아니라, id로
    등급을 정하면 다른 앱이 같은 id로 사내 등급을 받는다(RFC 8252 8.6, ADR-24).
    """
    tier = (token.claims or {}).get("client_tier") if token else None
    return ClientTier.INTERNAL if tier == ClientTier.INTERNAL.value else ClientTier.EXTERNAL


class JwtVerifier:
    """mcp SDK의 TokenVerifier. keys를 바꿔 끼우면 네트워크 없이 테스트할 수 있다."""

    def __init__(self, issuer: str, resource: str, keys: PyJWKClient):
        self.issuer = issuer
        self.resource = resource
        self.keys = keys

    @classmethod
    def from_settings(cls, settings: Settings) -> "JwtVerifier":
        url = settings.jwks_url or f"{settings.auth_issuer.rstrip('/')}/oauth2/jwks"
        return cls(settings.auth_issuer, settings.resource_url,
                   PyJWKClient(url, lifespan=JWKS_CACHE_SECONDS, timeout=JWKS_TIMEOUT_SECONDS))

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await anyio.to_thread.run_sync(self._verify, token)
        except jwt.PyJWKClientConnectionError as e:
            log.warning("JWKS를 가져오지 못해 토큰을 거절했다: %r", str(e))
        except jwt.PyJWTError as e:
            # 메시지에 토큰 헤더의 kid처럼 요청자가 넣은 값이 들어가므로 %r로 줄바꿈을 막는다
            log.info("액세스 토큰을 거절했다: %r", str(e))
        except Exception:
            log.exception("액세스 토큰을 검증하다 예상하지 못한 오류가 났다")
        return None

    def _verify(self, token: str) -> AccessToken:
        key = self.keys.get_signing_key_from_jwt(token)
        # 토큰 대상(aud)을 실제로 검사하는 곳은 이 audience 인자 하나뿐이다. 지우면 안 된다. 아래 AccessToken의
        # resource를 이 서버 주소로 채우므로, SDK의 validate_token_resource 비교는 늘 통과해 대신 막아 주지 않는다
        claims = jwt.decode(token, key, algorithms=ALGORITHMS, issuer=self.issuer, audience=self.resource,
                            leeway=LEEWAY_SECONDS, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        subject, client_id = claims["sub"], claims.get("client_id")
        # sub는 위키 API 경로에 들어가는 사용자 id다. user: 접두사나 경로 문자가 섞인 값은 받지 않는다
        if not isinstance(subject, str) or not valid_user_id(subject):
            raise jwt.InvalidTokenError("sub가 위키 사용자 id 형식이 아니다")
        if not isinstance(client_id, str) or not client_id:
            raise jwt.InvalidTokenError("client_id가 없다")
        # expires_at은 비워 둔다. 채우면 SDK가 허용 오차 없이 만료를 한 번 더 비교해 기준이 둘이 된다
        return AccessToken(token=token, client_id=client_id, scopes=scopes_of(claims), resource=self.resource,
                           subject=subject, claims=claims)
