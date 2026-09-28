"""환경 변수에서 읽는 설정. 기본값은 로컬 개발(M1) 기준이다."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# 임베딩 모델은 이름, 커밋, 불러올 형식을 함께 고정한다 (ADR-11, experiments/embedding-model).
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_DTYPE = "float32"
EMBEDDING_DIM = 1024

_DEV_SERVICE_TOKEN = "local-service-token"  # docker-compose.yml의 로컬 개발용 기본값
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _list(value: str | None) -> tuple[str, ...]:
    """쉼표로 구분한 목록. 빈 항목은 버린다."""
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    # 검색 인덱스용 데이터베이스. 위키 DB와 분리한다 (ADR-01, ADR-02 "단순화할 때")
    database_url: str = "postgresql://wiki:wiki@127.0.0.1:5433/wiki_search"
    index_alias: str = "wiki-chunks"
    # 검색 서버가 그룹을 해석하고 본문을 재확인할 곳. wiki: Spring 위키 API, file: data/wiki 파일(평가·테스트용)
    wiki_source: str = "wiki"
    wiki_dir: Path = Path("data/wiki")
    # 전송 (ADR-06). stdio: 실행 환경이 사용자와 등급을 정한다. http: 요청마다 OAuth 액세스 토큰으로 정한다
    transport: str = "stdio"
    user: str | None = None  # stdio 전용. 실행 환경이 사용자를 정한다 (ADR-06)
    client_tier: str | None = None  # stdio 전용. 비면 외부. OAuth가 없으면 가장 좁은 등급을 기본으로 둔다 (ADR-17)
    # HTTP 전송 (M5). resource_url은 이 MCP 서버의 주소로, 토큰의 aud(RFC 8707 resource)와 정확히 같아야 하고
    # 경로가 MCP 엔드포인트가 된다. 인가 서버 설정은 wiki-service/README.md "인가 서버"와 같은 값을 쓴다
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    resource_url: str = "http://127.0.0.1:8000/mcp"
    auth_issuer: str = "http://127.0.0.1:8081"  # 토큰의 iss와 정확히 같아야 한다
    jwks_url: str | None = None  # 비면 {auth_issuer}/oauth2/jwks
    # 클라이언트 신뢰 등급은 설정으로 두지 않는다. 인가 서버가 정해 토큰의 client_tier 클레임에 넣는다 (ADR-17, ADR-24)
    # Host·Origin 헤더 검사 (DNS 리바인딩 방어). allowed_hosts가 비면 resource_url의 호스트만 받는다.
    # allowed_origins가 비면 Origin 헤더를 보낸 요청(브라우저)은 모두 막는다
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    # Spring 위키 서비스 (wiki-service/README.md). 토큰 기본값은 docker-compose.yml의 로컬 개발용 값과 같다.
    # 클라이언트 토큰을 위키에 넘기지 않고 서비스 자격 증명을 따로 쓴다 (ADR-06)
    wiki_api_url: str = "http://127.0.0.1:8081"
    wiki_service_token: str = _DEV_SERVICE_TOKEN
    wiki_admin_token: str = "local-admin-token"  # 가상 위키를 옮겨 넣는 wiki-rag-seed만 쓴다
    demo_password: str | None = None  # wiki-rag-seed가 옮긴 사용자 모두에게 정할 로그인 비밀번호 (M5 데모용)
    # 인덱싱 이벤트 (ADR-09). 파티션 수는 위키 서비스의 WIKI_EVENT_PARTITIONS와 같아야 한다
    redis_url: str = "redis://127.0.0.1:6380/0"
    event_partitions: int = 4

    def __post_init__(self):
        # 오타가 난 값을 조용히 기본값으로 읽으면 어느 위키로 권한을 판단하는지 모르게 된다
        if self.wiki_source not in ("wiki", "file"):
            raise ValueError(f"WIKI_SOURCE는 wiki나 file이어야 한다: {self.wiki_source!r}")
        # 로컬 개발용 토큰은 저장소에 공개돼 있다. 로컬이 아닌 위키에 그대로 붙으면 누구나 아는 토큰으로 인증하는 셈이다
        if urlsplit(self.wiki_api_url).hostname not in _LOCAL_HOSTS and self.wiki_service_token == _DEV_SERVICE_TOKEN:
            raise ValueError("로컬이 아닌 위키에는 WIKI_SERVICE_TOKEN을 따로 지정해야 한다")
        if self.transport not in ("stdio", "http"):
            raise ValueError(f"WIKI_MCP_TRANSPORT는 stdio나 http여야 한다: {self.transport!r}")
        if self.transport == "http":
            # HTTP에서는 사용자와 등급을 토큰으로만 정한다. 조용히 무시하면 WIKI_USER로 접속한다고 잘못 믿게 된다
            if self.user or self.client_tier:
                raise ValueError("HTTP 모드에서는 WIKI_USER와 WIKI_CLIENT_TIER를 쓰지 않는다. 액세스 토큰으로 정한다")
            if not urlsplit(self.resource_url).path.strip("/"):
                raise ValueError(f"WIKI_MCP_RESOURCE_URL에 MCP 엔드포인트 경로가 없다: {self.resource_url!r}")

    @classmethod
    def from_env(cls) -> "Settings":
        # 예전에는 이 목록의 client_id를 사내로 봤다. 공개 클라이언트의 id는 비밀이 아니라서(RFC 8252 8.6) 등급을
        # 인가 서버로 옮겼다. 남은 값을 조용히 무시하면 운영자가 등급이 바뀐 것을 모른다
        if "WIKI_MCP_INTERNAL_CLIENTS" in os.environ:
            raise ValueError("WIKI_MCP_INTERNAL_CLIENTS는 없어졌다. 등급은 인가 서버가 토큰의 client_tier로 정한다. "
                             "환경에서 지우고, 사내 클라이언트는 위키 인가 서버에 기밀 클라이언트로 등록한다")
        return cls(
            database_url=os.environ.get("DATABASE_URL", cls.database_url),
            index_alias=os.environ.get("WIKI_INDEX_ALIAS", cls.index_alias),
            wiki_source=os.environ.get("WIKI_SOURCE", cls.wiki_source),
            wiki_dir=Path(os.environ.get("WIKI_DIR", str(cls.wiki_dir))),
            transport=os.environ.get("WIKI_MCP_TRANSPORT", cls.transport),
            user=os.environ.get("WIKI_USER") or None,
            client_tier=os.environ.get("WIKI_CLIENT_TIER") or None,
            http_host=os.environ.get("WIKI_MCP_HOST", cls.http_host),
            http_port=int(os.environ.get("WIKI_MCP_PORT", cls.http_port)),
            resource_url=os.environ.get("WIKI_MCP_RESOURCE_URL", cls.resource_url),
            auth_issuer=os.environ.get("WIKI_AUTH_ISSUER", cls.auth_issuer),
            jwks_url=os.environ.get("WIKI_AUTH_JWKS_URL") or None,
            allowed_hosts=_list(os.environ.get("WIKI_MCP_ALLOWED_HOSTS")),
            allowed_origins=_list(os.environ.get("WIKI_MCP_ALLOWED_ORIGINS")),
            wiki_api_url=os.environ.get("WIKI_API_URL", cls.wiki_api_url),
            wiki_service_token=os.environ.get("WIKI_SERVICE_TOKEN", cls.wiki_service_token),
            wiki_admin_token=os.environ.get("WIKI_ADMIN_TOKEN", cls.wiki_admin_token),
            demo_password=os.environ.get("WIKI_DEMO_PASSWORD") or None,
            redis_url=os.environ.get("REDIS_URL", cls.redis_url),
            event_partitions=int(os.environ.get("WIKI_EVENT_PARTITIONS", cls.event_partitions)),
        )
