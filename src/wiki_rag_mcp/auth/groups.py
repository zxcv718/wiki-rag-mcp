"""그룹 멤버십 캐시 (ADR-08). 검색마다 위키에 그룹을 묻지 않도록 Redis에 60초 둔다.

멤버십이 바뀌면 위키가 `wiki:membership`에 이벤트를 내고, 인덱서 워커가 받아 `invalidate`를 부른다.
검색 서버는 stdio에서 클라이언트마다 따로 뜨므로, 캐시를 Redis 한 곳에 두어야 한 번 지워서 모두에 반영된다.

키를 지우기만 하면 막지 못하는 순서가 있다.
  1. 서버가 캐시를 못 찾고 위키에서 그룹을 읽는다(옛 멤버십).
  2. 그사이 그룹에서 빠지는 변경이 커밋되고, 무효화가 먼저 끝난다.
  3. 서버가 1에서 읽은 옛 그룹을 캐시에 쓴다.
그러면 그룹에서 빠진 사람이 TTL(60초) 동안 그 그룹의 문서를 검색으로 본다. 그래서 사용자마다 세대 번호를 두고
무효화할 때 올린다. 캐시 값에는 위키를 읽기 전에 본 번호를 함께 적고, 읽을 때 번호가 지금과 다르면 버린다.
Facebook의 memcache가 lease로 막는 "stale set"과 같은 문제이고, 설계서 8장의 결과 캐시 세대 번호와 같은 방식이다.

- 세대 번호 키에는 TTL을 두지 않는다. 번호가 사라져 0으로 돌아가면 무효화한 캐시가 다시 맞는 값이 된다.
  Redis의 maxmemory-policy는 noeviction이나 volatile-*여야 한다(TTL 없는 키를 지우지 않는 정책).
- 위키에 없는 사용자와 그룹 조회 실패는 캐시하지 않는다. 사용자 생성은 이벤트가 없어서, "없는 사용자"를
  캐시하면 방금 만든 사용자가 60초 동안 막힌다.
- Redis를 읽거나 쓰지 못하면 캐시 없이 위키에 묻는다. 캐시를 건너뛴 값은 늘 더 새롭기 때문에 권한이 넓어질 일이
  없고, 느려질 뿐이다.
"""

import json
import logging

import redis
from opentelemetry import metrics
from redis.backoff import NoBackoff
from redis.retry import Retry

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.search.filters import valid_groups
from wiki_rag_mcp.wiki.source import GroupSource

log = logging.getLogger("wiki_rag_mcp.auth.groups")

KEY_PREFIX = "wiki:groups"
TTL_SECONDS = 60
# 검색 지연 예산에서 권한 해석 몫이 20ms다(8장). Redis가 멈춰도 요청이 따라 멈추지 않게 짧게 끊고 위키로 간다
REDIS_TIMEOUT_SECONDS = 0.5

# 캐시 적중률(ADR-15). 적중하지 못하면 권한 해석이 위키 호출만큼 느려진다(8장 지연 예산)
_lookups = metrics.get_meter(__name__).create_counter(
    "wiki.group_cache.lookups", unit="{lookup}",
    description="그룹 캐시 조회. result: hit(캐시), miss(위키에 물음), error(Redis를 못 읽어 위키에 물음)")


def cache_key(user_id: str) -> str:
    return f"{KEY_PREFIX}:{user_id}"


def generation_key(user_id: str) -> str:
    return f"{KEY_PREFIX}:{user_id}:gen"


def open_client(settings: Settings) -> redis.Redis:
    # 재시도하지 않는다. 캐시를 못 쓰면 위키로 가면 되는데, 재시도와 백오프를 기다리면 검색이 수 초씩 멈춘다
    return redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=REDIS_TIMEOUT_SECONDS,
                                socket_connect_timeout=REDIS_TIMEOUT_SECONDS, retry=Retry(NoBackoff(), 0))


def invalidate(client: redis.Redis, user_id: str) -> None:
    """멤버십이 바뀐 사용자의 캐시를 버린다. 번호를 올리는 것이 핵심이고, 값을 지우는 것은 메모리를 비우는 일이다.

    번호 키가 정수가 아니게 깨져 있으면 두 키를 모두 지운다. 예외로 올리면 워커가 같은 이벤트에서 계속 죽어
    문서 이벤트까지 멈춘다.
    """
    pipe = client.pipeline(transaction=True)
    pipe.incr(generation_key(user_id))
    pipe.delete(cache_key(user_id))
    try:
        pipe.execute()
    except redis.ResponseError as e:
        log.error("그룹 캐시 세대 번호가 깨져 있어 지운다 user_id=%s: %s", user_id, e)
        client.delete(generation_key(user_id), cache_key(user_id))


class GroupCache:
    def __init__(self, source: GroupSource, client: redis.Redis, ttl: int = TTL_SECONDS):
        self.source = source
        self.client = client
        self.ttl = ttl

    def _cached(self, user_id: str) -> tuple[int, list[str] | None] | None:
        """(지금 세대 번호, 캐시된 그룹). Redis를 읽지 못하거나 번호가 깨져 있으면 None이고, 이때는 캐시에 쓰지 않는다.

        캐시에서 읽은 그룹도 위키 응답처럼 형식을 다시 확인한다. 그룹 자리에 user:나 all이 들어 있으면 버린다.
        """
        try:
            generation, raw = self.client.mget(generation_key(user_id), cache_key(user_id))
            current = int(generation or 0)
        except redis.RedisError as e:
            log.warning("그룹 캐시를 읽지 못해 위키에 바로 묻는다: %r", e)
            return None
        except ValueError:
            log.error("그룹 캐시 세대 번호가 정수가 아니다 user_id=%s", user_id)
            return None
        try:
            entry = json.loads(raw) if raw else None
        except ValueError:
            entry = None
        if isinstance(entry, dict) and entry.get("gen") == current and valid_groups(entry.get("groups")):
            return current, entry["groups"]
        return current, None

    def groups_of(self, user_id: str) -> list[str]:
        cached = self._cached(user_id)
        if cached is not None and cached[1] is not None:
            _lookups.add(1, {"result": "hit"})
            return list(cached[1])
        _lookups.add(1, {"result": "miss" if cached is not None else "error"})
        groups = self.source.groups_of(user_id)  # 모르는 사용자와 조회 실패는 캐시하지 않고 그대로 올린다
        if cached is not None:
            try:
                self.client.set(cache_key(user_id), json.dumps({"gen": cached[0], "groups": groups}), ex=self.ttl)
            except redis.RedisError as e:
                log.warning("그룹 캐시를 쓰지 못했다: %r", e)
        return groups
