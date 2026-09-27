"""권한을 회수한 뒤 검색에서 사라지기까지 걸린 시간과 그룹 해석 지연 (4장 "검증 방법"의 권한 회수, ADR-07, ADR-08).

MCP 도구(search_wiki, get_document)를 서버와 같은 구성으로 부른다. 위키는 Spring 위키 API, 그룹은 Redis 캐시,
검색은 실제 인덱스(wiki-chunks)와 bge-m3다. 관리자 API로 권한을 바꾸고, 응답을 받은 시각부터 search_wiki
결과에서 그 문서가 사라질 때까지를 잰다. 회수 직후 get_document가 막히는지도 함께 기록한다.

- 문서 제한 변경: 위키, 아웃박스, Redis Streams, 인덱서(권한 필드만 고침)를 거친다.
- 그룹 멤버십 변경: 인덱스는 그대로이고, 워커가 멤버십 이벤트로 그룹 캐시를 무효화한다.

전제: 아래가 모두 떠 있어야 한다.

    docker compose up -d
    uv run wiki-rag-seed
    uv run wiki-rag-worker          # 다른 터미널에서, 실제 bge-m3로

    uv run python experiments/m4-permissions/revocation.py

측정용 사용자와 문서를 새로 만들어 쓰고, 끝나면 문서를 지우고 사용자를 그룹에서 뺀다. 시드 문서는 건드리지 않는다.
같은 질의를 반복해서 보내므로 질의 임베딩은 한 번만 계산해 둔다. 잴 것은 권한 반영이지 임베딩이 아니기 때문이다.
"""

import json
import math
import statistics
import time
import uuid
from pathlib import Path

import anyio
import httpx
from mcp import Client

from wiki_rag_mcp.auth.groups import GroupCache, cache_key, generation_key, invalidate, open_client
from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.indexing.embedder import Embedder
from wiki_rag_mcp.search.backend import open_store
from wiki_rag_mcp.server.app import Services, build_server
from wiki_rag_mcp.server.responses import NOT_FOUND
from wiki_rag_mcp.wiki.http import HttpWikiSource

REPEAT = 20
GAP_SECONDS = 1.0
TIMEOUT_SECONDS = 120.0
SPACE, GROUP = "engineering", "eng"  # engineering 스페이스는 eng 그룹만 본다
LOOKUPS = 200
OUT = Path(__file__).parent / "results"


class RememberQueries:
    """같은 질의의 임베딩을 다시 계산하지 않는다. 검색과 권한 필터는 매번 실제로 돈다."""

    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.vectors: dict[str, list[float]] = {}

    def encode_query(self, text: str) -> list[float]:
        if text not in self.vectors:
            self.vectors[text] = self.embedder.encode_query(text)
        return self.vectors[text]


class Lab:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.admin = httpx.Client(base_url=settings.wiki_api_url, timeout=30,
                                  headers={"Authorization": f"Bearer {settings.wiki_admin_token}"})
        self.source = HttpWikiSource(settings.wiki_api_url, settings.wiki_service_token)
        self.redis = open_client(settings)
        self.user = f"m4-{uuid.uuid4().hex[:8]}"
        self.groups = GroupCache(self.source, self.redis)
        self.services = Services(Settings(user=self.user), self.source, open_store(settings),
                                 RememberQueries(Embedder()), self.groups)
        self.docs: list[str] = []

    def call(self, method: str, path: str, **kwargs) -> dict:
        response = self.admin.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def tool(self, name: str, args: dict):
        async def go():
            async with Client(build_server(self.services)) as client:
                return await client.call_tool(name, args)

        return anyio.run(go)

    def title(self, doc_id: str) -> str:
        return f"권한 회수 측정 문서 {doc_id}"

    def searchable(self, doc_id: str) -> bool:
        result = self.tool("search_wiki", {"query": self.title(doc_id), "top_k": 5})
        if result.is_error:
            raise RuntimeError(result.content[0].text)
        return doc_id in {r["doc_id"] for r in result.structured_content["results"]}

    def readable(self, doc_id: str) -> bool:
        result = self.tool("get_document", {"doc_id": doc_id})
        if result.is_error and not result.content[0].text.endswith(NOT_FOUND):
            raise RuntimeError(result.content[0].text)
        return not result.is_error

    def wait(self, condition, what: str) -> float:
        started = time.monotonic()
        while not condition():
            if time.monotonic() - started > TIMEOUT_SECONDS:
                raise TimeoutError(f"{what}: {TIMEOUT_SECONDS}초 안에 반영되지 않았다")
            time.sleep(0.05)
        return time.monotonic() - started

    def create(self) -> str:
        doc_id = f"m4-{uuid.uuid4().hex[:10]}"
        self.call("POST", "/admin/documents", json={
            "doc_id": doc_id, "space": SPACE, "title": self.title(doc_id),
            "body": f"# {self.title(doc_id)}\n\n권한을 회수하면 검색 결과에서 사라져야 하는 측정용 문서다."})
        self.docs.append(doc_id)
        self.wait(lambda: self.searchable(doc_id), f"{doc_id} 생성")
        return doc_id

    def delete(self, doc_id: str) -> None:
        # 제목이 거의 같은 측정용 문서가 쌓이면 새 문서가 자기 제목 검색의 상위 5개에서 밀려나므로 바로 지운다
        self.call("DELETE", f"/admin/documents/{doc_id}")
        self.docs.remove(doc_id)

    def revoke_restriction(self) -> dict:
        doc_id = self.create()
        self.call("PUT", f"/admin/documents/{doc_id}/restrictions", json={"principals": ["group:dba"]})
        blocked = not self.readable(doc_id)
        seconds = self.wait(lambda: not self.searchable(doc_id), f"{doc_id} 제한 변경")
        self.delete(doc_id)
        return {"scenario": "문서 제한 변경", "doc_id": doc_id, "body_blocked_at_once": blocked, "seconds": seconds}

    def remove_member(self) -> list[dict]:
        doc_id = self.create()  # 이 검색으로 그룹이 캐시에 올라간다
        self.call("DELETE", f"/admin/groups/{GROUP}/members/{self.user}")
        blocked = not self.readable(doc_id)
        removed = self.wait(lambda: not self.searchable(doc_id), f"{doc_id} 그룹에서 제거")
        self.call("PUT", f"/admin/groups/{GROUP}/members/{self.user}")
        granted = self.wait(lambda: self.searchable(doc_id), f"{doc_id} 그룹에 다시 추가")
        self.delete(doc_id)
        return [{"scenario": "그룹에서 제거", "doc_id": doc_id, "body_blocked_at_once": blocked, "seconds": removed},
                {"scenario": "그룹에 다시 추가", "doc_id": doc_id, "seconds": granted}]

    def group_lookups(self) -> list[dict]:
        """그룹 해석 지연. 캐시를 비운 뒤 한 번(위키 호출), 곧바로 한 번 더(캐시 적중) 잰다."""
        rows = []
        for _ in range(LOOKUPS):
            invalidate(self.redis, self.user)
            for kind in ("위키 호출", "캐시 적중"):
                started = time.perf_counter()
                self.groups.groups_of(self.user)
                rows.append({"scenario": f"그룹 해석 ({kind})", "seconds": time.perf_counter() - started})
        return rows

    def cleanup(self) -> None:
        for doc_id in self.docs:
            self.admin.delete(f"/admin/documents/{doc_id}")
        self.admin.delete(f"/admin/groups/{GROUP}/members/{self.user}")
        self.redis.delete(cache_key(self.user), generation_key(self.user))


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def main() -> None:
    lab = Lab(Settings.from_env())
    lab.call("PUT", f"/admin/users/{lab.user}", json={"name": "권한 회수 측정"})
    lab.call("PUT", f"/admin/groups/{GROUP}/members/{lab.user}")
    rows: list[dict] = []
    try:
        lab.searchable("warmup")  # 모델과 연결을 먼저 데운다
        for i in range(REPEAT):
            rows.append(lab.revoke_restriction())
            time.sleep(GAP_SECONDS)
            rows.extend(lab.remove_member())
            time.sleep(GAP_SECONDS)
            print(f"{i + 1}/{REPEAT}", flush=True)
        rows.extend(lab.group_lookups())
    finally:
        lab.cleanup()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "revocation.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                          encoding="utf-8")
    summary = {}
    for scenario in dict.fromkeys(r["scenario"] for r in rows):
        mine = [r for r in rows if r["scenario"] == scenario]
        secs = [r["seconds"] for r in mine]
        summary[scenario] = {"n": len(secs), "p50": statistics.median(secs), "p95": percentile(secs, 0.95),
                             "max": max(secs)}
        if "body_blocked_at_once" in mine[0]:
            summary[scenario]["body_blocked_at_once"] = sum(r["body_blocked_at_once"] for r in mine)
    (OUT / "revocation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("| 시나리오 | 건수 | p50 | p95 | 최대 | 본문 즉시 차단 |\n|---|---|---|---|---|---|")
    for scenario, s in summary.items():
        unit, scale = ("ms", 1000) if "그룹 해석" in scenario else ("초", 1)
        blocked = f"{s['body_blocked_at_once']}/{s['n']}" if "body_blocked_at_once" in s else "-"
        print(f"| {scenario} | {s['n']} | {s['p50'] * scale:.2f}{unit} | {s['p95'] * scale:.2f}{unit} | "
              f"{s['max'] * scale:.2f}{unit} | {blocked} |")


if __name__ == "__main__":
    main()
