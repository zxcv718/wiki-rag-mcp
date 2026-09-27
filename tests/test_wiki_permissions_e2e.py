"""권한을 회수하면 검색에서 사라지는지를 실제 위키, 아웃박스, Redis Streams, 그룹 캐시로 확인한다
(4장 "검증 방법"의 권한 회수, ADR-07, ADR-08).

- 문서 제한 변경: 위키가 ACL_CHANGED를 내고 인덱서가 권한 필드를 고치면 검색에서 사라진다.
- 그룹 멤버십 변경: 인덱스는 그대로이고, 워커가 멤버십 이벤트로 그룹 캐시를 무효화하면 다음 검색부터 사라진다.
- 두 경우 모두 본문(get_document)은 위키가 지금 권한으로 다시 판단하므로 회수 직후부터 막힌다.

전제: `docker compose up -d`와 `uv run wiki-rag-seed`. 이 테스트는 워커를 같은 프로세스에서 따로 돌리고
검색 인덱스도 임시 테이블을 쓴다. 떠 있는 실제 워커와 이벤트를 나눠 먹지 않도록 소비자 그룹을 따로 만들고
(새 이벤트부터 읽음) 끝나면 지운다. 권한 변경은 임베딩을 하지 않아 가짜 인코더로 충분하다.

실제 스트림에 그룹을 붙이므로 정리에 신경 쓴다. 이벤트는 모든 그룹이 확인해야 지워지므로(XACKDEL ACKED), 이
그룹이 남으면 실제 인덱서가 처리한 이벤트도 스트림에 쌓인다. 그래서 이 그룹이 자기 몫을 다 처리한 뒤에 지우고,
이전 실행이 남긴 그룹도 시작할 때 지운다. 위키에 만든 측정용 사용자는 지우는 API가 없어 남는다.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

import anyio
import httpx
import pytest
from mcp import Client

from tests.fakeencoder import DIM, FakeEncoder, count_words
from tests.pg import new_store
from tests.services import redis_client, wiki_source
from wiki_rag_mcp.auth.groups import GroupCache, cache_key, generation_key, invalidate, open_client
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.events import MEMBERSHIP_STREAM, stream_name
from wiki_rag_mcp.indexing.incremental import apply_event
from wiki_rag_mcp.indexing.worker import StreamWorker
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.server.responses import NOT_FOUND

pytestmark = pytest.mark.integration
TARGET_SECONDS = 30.0  # 문서 수정 후 검색 반영 목표 (1장)
SPACE, GROUP = "engineering", "eng"  # engineering 스페이스는 eng 그룹만 본다
CONSUMER_PREFIX = "e2e-"


@dataclass
class Lab:
    admin: httpx.Client
    services: Services
    user: str
    docs: list[str] = field(default_factory=list)

    def call(self, method: str, path: str, **kwargs) -> dict:
        response = self.admin.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def create(self, restricted: list[str]) -> str:
        doc_id = f"e2e-{uuid.uuid4().hex[:10]}"
        self.call("POST", "/admin/documents", json={
            "doc_id": doc_id, "space": SPACE, "title": f"권한 회수 시험 {doc_id}",
            "body": f"# 권한 회수 시험 {doc_id}\n\n회수하면 검색에서 사라져야 하는 문서다.",
            "restricted_principals": restricted})
        self.docs.append(doc_id)
        return doc_id

    def tool(self, name: str, args: dict):
        async def go():
            async with Client(build_server(self.services)) as client:
                return await client.call_tool(name, args)

        return anyio.run(go)

    def searchable(self, doc_id: str) -> bool:
        result = self.tool("search_wiki", {"query": f"권한 회수 시험 {doc_id}", "top_k": 10})
        assert not result.is_error, result.content
        return doc_id in {r["doc_id"] for r in result.structured_content["results"]}

    def readable(self, doc_id: str) -> bool:
        result = self.tool("get_document", {"doc_id": doc_id})
        if result.is_error:
            assert result.content[0].text.endswith(NOT_FOUND), result.content
            return False
        return True

    def wait_until(self, condition, timeout: float = TARGET_SECONDS) -> float:
        started = time.monotonic()
        while not condition():
            if time.monotonic() - started > timeout:
                pytest.fail(f"{timeout}초 안에 반영되지 않았다")
            time.sleep(0.1)
        return time.monotonic() - started


def streams(settings: Settings) -> list[str]:
    return [*(stream_name(p) for p in range(settings.event_partitions)), MEMBERSHIP_STREAM]


def settled(client, group: str, names: list[str]) -> bool:
    """이 그룹이 스트림마다 읽을 것도, 확인하지 않은 것도 없는가."""
    for stream in names:
        info = next((g for g in client.xinfo_groups(stream) if g["name"] == group), None)
        if info and (info["pending"] or info.get("lag")):
            return False
    return True


@pytest.fixture(scope="module")
def lab():
    settings = Settings()
    source, client = wiki_source(), redis_client()
    names = streams(settings)
    for stream in names:  # 이전 실행이 중간에 죽어 남긴 그룹
        if client.exists(stream):
            for g in client.xinfo_groups(stream):
                if g["name"].startswith(CONSUMER_PREFIX):
                    client.xgroup_destroy(stream, g["name"])
    store = new_store("e2e_perm", DIM)
    group = f"{CONSUMER_PREFIX}{uuid.uuid4().hex[:8]}"
    # 그룹을 다시 만들어야 할 때도 새 이벤트부터 읽는다. 0부터 읽으면 실제 스트림의 이벤트를 모두 다시 처리한다
    worker = StreamWorker(client, lambda e: apply_event(e, source, store, FakeEncoder(), count_words),
                          range(settings.event_partitions), membership=lambda user_id: invalidate(client, user_id),
                          group=group, consumer=group, start_id="$")
    worker.ensure_groups()
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(stop,), kwargs={"block_ms": 200}, daemon=True)
    thread.start()

    admin = httpx.Client(base_url=settings.wiki_api_url, timeout=30,
                         headers={"Authorization": f"Bearer {settings.wiki_admin_token}"})
    user = f"e2e-{uuid.uuid4().hex[:8]}"
    services = Services(Settings(user=user), source, store, FakeEncoder(), GroupCache(source, open_client(settings)))
    lab = Lab(admin, services, user)
    lab.call("PUT", f"/admin/users/{user}", json={"name": "권한 회수 시험"})
    lab.call("PUT", f"/admin/groups/{GROUP}/members/{user}")
    try:
        yield lab
    finally:
        for doc_id in lab.docs:
            admin.delete(f"/admin/documents/{doc_id}")
        admin.delete(f"/admin/groups/{GROUP}/members/{user}")
        # 이 그룹이 자기 몫(방금 지운 문서와 멤버십 이벤트까지)을 다 처리한 뒤에 멈추고 지운다
        deadline = time.monotonic() + 15
        while not settled(client, group, names) and time.monotonic() < deadline:
            time.sleep(0.2)
        stop.set()
        thread.join(timeout=15)
        alive = thread.is_alive()
        if not alive:  # 워커가 살아 있으면 지운 그룹을 다시 만들 수 있어 남겨 두고, 다음 실행이 시작할 때 지운다
            for stream in names:
                client.xgroup_destroy(stream, group)
        client.delete(cache_key(user), generation_key(user))
        store.drop()
        admin.close()
        assert not alive, "테스트 워커가 멈추지 않았다"


def test_revoked_document_restriction_disappears_from_search(lab):
    doc_id = lab.create(["all"])
    lab.wait_until(lambda: lab.searchable(doc_id))

    lab.call("PUT", f"/admin/documents/{doc_id}/restrictions", json={"principals": ["group:dba"]})
    assert not lab.readable(doc_id)  # 본문은 위키가 바로 막는다 (ADR-07 본문 재확인)
    seconds = lab.wait_until(lambda: not lab.searchable(doc_id))
    assert seconds < TARGET_SECONDS


def test_removed_group_member_loses_access_through_cache_invalidation(lab):
    """그룹에서 빠지면 인덱스는 그대로이고 그룹 캐시만 무효화된다 (ADR-08). TTL(60초)을 기다리지 않는다."""
    doc_id = lab.create(["all"])
    lab.wait_until(lambda: lab.searchable(doc_id))
    lab.services.principals()  # 그룹을 캐시에 올린 상태에서 뺀다. 무효화가 없으면 이 캐시가 60초 남는다
    assert lab.services.groups.client.exists(cache_key(lab.user))

    lab.call("DELETE", f"/admin/groups/{GROUP}/members/{lab.user}")
    assert not lab.readable(doc_id)
    seconds = lab.wait_until(lambda: not lab.searchable(doc_id))
    assert seconds < 10, "캐시 TTL이 아니라 멤버십 이벤트로 무효화돼야 한다"

    lab.call("PUT", f"/admin/groups/{GROUP}/members/{lab.user}")  # 다시 넣으면 다시 보인다
    lab.wait_until(lambda: lab.searchable(doc_id), timeout=10)
    assert lab.readable(doc_id)
