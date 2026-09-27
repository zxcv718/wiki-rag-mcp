"""환경 변수에서 읽는 설정. 기본값은 로컬 개발(M1) 기준이다."""

import os
from dataclasses import dataclass
from pathlib import Path

# 임베딩 모델은 이름, 커밋, 불러올 형식을 함께 고정한다 (ADR-11, experiments/embedding-model).
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_DTYPE = "float32"
EMBEDDING_DIM = 1024


@dataclass(frozen=True)
class Settings:
    # 검색 인덱스용 데이터베이스. 위키 DB와 분리한다 (ADR-01, ADR-02 "단순화할 때")
    database_url: str = "postgresql://wiki:wiki@127.0.0.1:5433/wiki_search"
    index_alias: str = "wiki-chunks"
    wiki_dir: Path = Path("data/wiki")
    user: str | None = None  # stdio에서는 실행 환경이 사용자를 정한다 (ADR-06)
    client_tier: str = "external"  # M1에는 OAuth가 없어 가장 좁은 등급을 기본으로 둔다 (ADR-17)
    # Spring 위키 서비스 (wiki-service/README.md). 토큰 기본값은 docker-compose.yml의 로컬 개발용 값과 같다.
    # 클라이언트 토큰을 위키에 넘기지 않고 서비스 자격 증명을 따로 쓴다 (ADR-06)
    wiki_api_url: str = "http://127.0.0.1:8081"
    wiki_service_token: str = "local-service-token"
    wiki_admin_token: str = "local-admin-token"  # 가상 위키를 옮겨 넣는 wiki-rag-seed만 쓴다
    # 인덱싱 이벤트 (ADR-09). 파티션 수는 위키 서비스의 WIKI_EVENT_PARTITIONS와 같아야 한다
    redis_url: str = "redis://127.0.0.1:6380/0"
    event_partitions: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL", cls.database_url),
            index_alias=os.environ.get("WIKI_INDEX_ALIAS", cls.index_alias),
            wiki_dir=Path(os.environ.get("WIKI_DIR", str(cls.wiki_dir))),
            user=os.environ.get("WIKI_USER") or None,
            client_tier=os.environ.get("WIKI_CLIENT_TIER", cls.client_tier),
            wiki_api_url=os.environ.get("WIKI_API_URL", cls.wiki_api_url),
            wiki_service_token=os.environ.get("WIKI_SERVICE_TOKEN", cls.wiki_service_token),
            wiki_admin_token=os.environ.get("WIKI_ADMIN_TOKEN", cls.wiki_admin_token),
            redis_url=os.environ.get("REDIS_URL", cls.redis_url),
            event_partitions=int(os.environ.get("WIKI_EVENT_PARTITIONS", cls.event_partitions)),
        )
