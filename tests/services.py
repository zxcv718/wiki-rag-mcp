"""통합 테스트가 쓰는 로컬 서비스(docker compose) 연결 도우미.

서비스가 떠 있지 않으면 로컬에서는 건너뛰지만, CI는 REQUIRE_SERVICES=1로 실패시킨다. 서비스가 뜨지 않았는데
통합 테스트가 모두 건너뛰어져 CI가 초록불이 되면, 권한 테스트셋이 돌지 않은 채 병합되기 때문이다.
"""

import os

import pytest

from wiki_rag_mcp.config import Settings


def unavailable(reason: str):
    if os.environ.get("REQUIRE_SERVICES"):
        pytest.fail(f"{reason} (REQUIRE_SERVICES=1)")
    pytest.skip(reason)


def redis_client():
    import redis

    client = redis.Redis.from_url(Settings().redis_url, decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError:
        unavailable("로컬 Redis가 떠 있지 않다 (docker compose up -d redis)")
    return client


def wiki_source():
    """시드한 위키 서비스(wiki-rag-seed)에 붙은 HttpWikiSource."""
    from wiki_rag_mcp.wiki.http import HttpWikiSource
    from wiki_rag_mcp.wiki.source import WikiUnavailableError

    settings = Settings()
    source = HttpWikiSource(settings.wiki_api_url, settings.wiki_service_token)
    try:
        source.space_title("company")
    except WikiUnavailableError:
        unavailable("위키 서비스가 떠 있지 않다 (docker compose up -d wiki && uv run wiki-rag-seed)")
    return source
