"""그룹 멤버십 캐시(ADR-08)를 실제 Redis로 확인한다. `docker compose up -d redis`로 띄운 뒤 실행한다.

캐시 키가 실제 서버와 겹치지 않게 테스트마다 새 사용자 id를 쓴다.
"""

import json
import time
import uuid
from collections.abc import Callable

import pytest

from tests.services import redis_client
from wiki_rag_mcp.auth.groups import TTL_SECONDS, GroupCache, cache_key, generation_key, invalidate, open_client
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.wiki.source import GroupLookupError, UnknownUserError

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    c = redis_client()
    yield c
    c.close()


@pytest.fixture
def user(client):
    user_id = f"cache-test-{uuid.uuid4().hex[:8]}"
    yield user_id
    client.delete(cache_key(user_id), generation_key(user_id))


class Wiki:
    """위키의 그룹 조회. 부를 때마다 센다."""

    def __init__(self, groups=("group:eng",)):
        self.groups = list(groups)
        self.calls = 0
        self.error: Exception | None = None
        self.during_read: Callable[[], None] | None = None  # 위키를 읽는 도중에 일어나는 일 (경쟁 상태 재현)

    def groups_of(self, user_id):
        self.calls += 1
        answer = list(self.groups)
        if self.during_read:
            self.during_read()
        if self.error:
            raise self.error
        return answer


def test_second_lookup_is_served_from_the_cache(client, user):
    wiki = Wiki()
    cache = GroupCache(wiki, client)
    assert cache.groups_of(user) == cache.groups_of(user) == ["group:eng"]
    assert wiki.calls == 1
    assert 0 < client.ttl(cache_key(user)) <= TTL_SECONDS


def test_membership_change_invalidates(client, user):
    wiki = Wiki()
    cache = GroupCache(wiki, client)
    cache.groups_of(user)
    wiki.groups = []  # 그룹에서 빠짐
    invalidate(client, user)
    assert cache.groups_of(user) == []
    assert wiki.calls == 2


def test_stale_read_finishing_after_invalidation_is_not_served(client, user):
    """옛 멤버십을 읽는 도중에 무효화가 끝나도, 그 옛 값이 다음 검색에 쓰이지 않는다 (stale set).

    키를 지우기만 하는 캐시는 이 순서에서 그룹에서 빠진 사람에게 TTL 동안 옛 그룹을 준다.
    """
    wiki = Wiki(groups=["group:dba"])
    cache = GroupCache(wiki, client)

    def removed_while_reading():
        wiki.groups = []
        wiki.during_read = None
        invalidate(client, user)

    wiki.during_read = removed_while_reading
    assert cache.groups_of(user) == ["group:dba"]  # 이 요청은 읽기 시작한 시점의 값을 받는다
    assert cache.groups_of(user) == []  # 다음 요청은 캐시에 쓰인 옛 값을 버리고 위키를 다시 읽는다
    assert wiki.calls == 2


@pytest.mark.parametrize("error", [UnknownUserError("없음"), GroupLookupError("장애")])
def test_failures_and_unknown_users_are_not_cached(client, user, error):
    """모르는 사용자를 캐시하면 방금 만든 사용자가 60초 동안 막힌다. 사용자 생성에는 이벤트가 없다."""
    wiki = Wiki()
    wiki.error = error
    cache = GroupCache(wiki, client)
    with pytest.raises(type(error)):
        cache.groups_of(user)
    wiki.error = None
    assert cache.groups_of(user) == ["group:eng"]
    assert wiki.calls == 2


@pytest.mark.parametrize("raw", ["not json", json.dumps(["group:eng"]), json.dumps({"gen": 0})])
def test_unreadable_cache_entry_is_a_miss(client, user, raw):
    client.set(cache_key(user), raw)
    wiki = Wiki(groups=["group:hr"])
    assert GroupCache(wiki, client).groups_of(user) == ["group:hr"]
    assert wiki.calls == 1


@pytest.mark.parametrize("groups", [["user:ceo"], ["all"], "group:eng", [1]])
def test_tampered_cache_entry_is_not_trusted(client, user, groups):
    """캐시에서 읽은 그룹도 위키 응답처럼 형식을 확인한다. 그룹 자리에 user:나 all이 있으면 권한이 넓어진다."""
    client.set(cache_key(user), json.dumps({"gen": 0, "groups": groups}))
    wiki = Wiki(groups=["group:hr"])
    assert GroupCache(wiki, client).groups_of(user) == ["group:hr"]


def test_corrupted_generation_is_a_miss_and_invalidation_repairs_it(client, user):
    """번호 키가 깨지면 캐시를 쓰지 않고 위키에 묻는다. 무효화가 예외로 워커를 멈추지 않고 키를 정리한다."""
    client.set(generation_key(user), "not-a-number")
    wiki = Wiki()
    cache = GroupCache(wiki, client)
    assert cache.groups_of(user) == cache.groups_of(user) == ["group:eng"]
    assert wiki.calls == 2 and not client.exists(cache_key(user))
    invalidate(client, user)
    assert not client.exists(generation_key(user))
    cache.groups_of(user)
    assert cache.groups_of(user) == ["group:eng"] and wiki.calls == 3


def test_redis_down_falls_back_to_the_wiki_without_waiting():
    """캐시를 못 쓰면 위키에 바로 묻는다. 위키의 값은 늘 캐시보다 새롭기 때문에 권한이 넓어질 일은 없다.

    서버의 Redis 클라이언트는 재시도하지 않는다. 재시도와 백오프를 기다리면 요청마다 수 초씩 멈춘다.
    """
    down = open_client(Settings(redis_url="redis://127.0.0.1:1/0"))
    wiki = Wiki()
    cache = GroupCache(wiki, down)
    started = time.perf_counter()
    assert cache.groups_of("anyone") == cache.groups_of("anyone") == ["group:eng"]
    assert time.perf_counter() - started < 1.0
    assert wiki.calls == 2
