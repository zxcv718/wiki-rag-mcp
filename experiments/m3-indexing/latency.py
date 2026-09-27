"""문서 수정 후 검색 반영까지 걸린 시간 (1장 목표: p95 30초 이하, 5장 "측정 지표").

위키 관리자 API로 문서를 바꾸고, 응답을 받은 시각부터 검색 DB의 문서 상태(doc_state)에 새 revision이 기록될
때까지를 잰다. 상태는 청크 교체와 같은 트랜잭션의 마지막에 쓰이므로, 기록된 순간부터 검색 결과에 반영돼 있다.
사용자가 겪는 지연(수정 직후 동료가 검색)과 같은 구간이다.

전제: 아래가 모두 떠 있어야 한다.

    docker compose up -d
    uv run wiki-rag-seed
    uv run wiki-rag-worker          # 다른 터미널에서, 실제 bge-m3로

    uv run python experiments/m3-indexing/latency.py

시드 문서 200개는 건드리지 않도록 측정용 문서를 따로 만들어 쓰고 마지막에 지운다. 스페이스 권한 변경(몰림)만
시드 문서에 닿는데, 권한을 바꾼 뒤 원래대로 되돌린다(내용은 그대로이고 revision만 오른다).
"""

import json
import math
import random
import statistics
import time
import uuid
from pathlib import Path

import httpx
import psycopg

from wiki_rag_mcp.config import Settings
from wiki_rag_mcp.search.pg_store import table_name
from wiki_rag_mcp.wiki.files import FileWikiSource

SEED = 20260927
REPEAT = 20
GAP_SECONDS = 1.0  # 평상시처럼 수정이 드문드문 들어오는 상황
TIMEOUT_SECONDS = 120.0
BURST_SPACE, BURST_VIEWERS = "engineering", ["group:eng"]


class Lab:
    def __init__(self, settings: Settings):
        self.admin = httpx.Client(base_url=settings.wiki_api_url, timeout=30,
                                  headers={"Authorization": f"Bearer {settings.wiki_admin_token}"})
        self.internal = httpx.Client(base_url=settings.wiki_api_url, timeout=30,
                                     headers={"Authorization": f"Bearer {settings.wiki_service_token}"})
        self.db = psycopg.connect(settings.database_url, autocommit=True)
        self.state_table = f"{table_name(settings.index_alias)}_doc_state"

    def call(self, method: str, path: str, **kwargs) -> dict:
        response = self.admin.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def wiki_revisions(self, doc_ids: set[str]) -> dict[str, int]:
        data = self.internal.get("/internal/revisions").raise_for_status().json()
        return {d["doc_id"]: d["revision"] for d in data["documents"] if d["doc_id"] in doc_ids}

    def wait(self, expected: dict[str, int], started: float) -> dict[str, float]:
        """문서마다 기대한 revision이 인덱스에 기록되기까지 걸린 초."""
        return {doc_id: at - started for doc_id, at in self.wait_until(expected, started).items()}

    def wait_until(self, expected: dict[str, int], started: float) -> dict[str, float]:
        """문서마다 기대한 revision이 인덱스에 기록된 시각(perf_counter)."""
        done: dict[str, float] = {}
        while len(done) < len(expected):
            if time.perf_counter() - started > TIMEOUT_SECONDS:
                raise TimeoutError(f"반영되지 않음: {sorted(set(expected) - set(done))[:5]}")
            rows = self.db.execute(f"SELECT doc_id, revision FROM {self.state_table} WHERE doc_id = ANY(%s)",
                                   [list(expected)]).fetchall()
            now = time.perf_counter()
            for doc_id, revision in rows:
                if doc_id not in done and revision >= expected[doc_id]:
                    done[doc_id] = now
            time.sleep(0.05)
        return done

    def measure(self, scenario: str, method: str, path: str, **kwargs) -> dict:
        started = time.perf_counter()
        doc = self.call(method, path, **kwargs)
        doc_id = doc.get("doc_id") or path.rsplit("/", 1)[-1]
        revision = doc.get("revision") or self.wiki_revisions({doc_id})[doc_id]
        seconds = self.wait({doc_id: revision}, started)[doc_id]
        return {"scenario": scenario, "doc_id": doc_id, "revision": revision, "seconds": round(seconds, 3)}


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]  # nearest-rank
    return {"n": len(values), "p50": round(statistics.median(values), 2), "p95": round(p95, 2),
            "max": round(max(values), 2)}


def main():
    settings = Settings.from_env()
    lab = Lab(settings)
    rng = random.Random(SEED)
    sources = rng.sample(FileWikiSource(settings.wiki_dir).documents(), REPEAT)
    # 삭제한 문서도 삭제 표시로 남으므로(tombstone), 다시 돌려도 겹치지 않게 실행마다 id를 새로 만든다
    run_id = uuid.uuid4().hex[:6]
    ids = [f"m3-lat-{run_id}-{i:02d}" for i in range(REPEAT)]
    records: list[dict] = []

    def run(scenario, requests):
        for method, path, kwargs in requests:
            records.append(lab.measure(scenario, method, path, **kwargs))
            print(records[-1])
            time.sleep(GAP_SECONDS)

    pairs = list(zip(ids, sources, strict=True))
    # 새 문서: 문서 전체를 임베딩한다. 본문은 가상 위키 문서를 빌려 쓴다
    run("create", [("POST", "/admin/documents", {"json": {
        "doc_id": i, "space": s.space, "title": f"{s.title} (측정용)", "body": s.body}}) for i, s in pairs])
    # 한 섹션 수정: 청크 하나만 임베딩한다 (ADR-10)
    appended = "\n\n수정 내용을 덧붙였습니다.\n"
    run("edit", [("PUT", f"/admin/documents/{i}", {"json": {
        "title": f"{s.title} (측정용)", "body": s.body.rstrip() + appended, "base_version": 1}}) for i, s in pairs])
    # 권한·등급: 임베딩 없이 권한 필드만 바꾼다
    run("restrict", [("PUT", f"/admin/documents/{i}/restrictions", {"json": {"principals": ["group:leads"]}})
                     for i in ids])
    run("classify", [("PUT", f"/admin/documents/{i}/classification", {"json": {"classification": "confidential"}})
                     for i in ids])
    run("delete", [("DELETE", f"/admin/documents/{i}", {}) for i in ids])

    # 새 문서 20개를 기다리지 않고 한꺼번에 만든다. 임베딩이 밀리는 경우라 가장 느린 경우에 가깝다
    burst_ids = [f"m3-lat-{run_id}-b{i:02d}" for i in range(REPEAT)]
    sent: dict[str, tuple[float, int]] = {}
    for doc_id, s in zip(burst_ids, sources, strict=True):
        started = time.perf_counter()
        doc = lab.call("POST", "/admin/documents", json={
            "doc_id": doc_id, "space": s.space, "title": f"{s.title} (측정용)", "body": s.body})
        sent[doc_id] = (started, doc["revision"])
    first = min(t for t, _ in sent.values())
    for doc_id, at in lab.wait_until({d: r for d, (_, r) in sent.items()}, first).items():
        records.append({"scenario": "create_burst", "doc_id": doc_id, "revision": sent[doc_id][1],
                        "seconds": round(at - sent[doc_id][0], 3)})
    deleted = {doc_id: lab.call("DELETE", f"/admin/documents/{doc_id}")["revision"] for doc_id in burst_ids}
    lab.wait(deleted, time.perf_counter())
    time.sleep(GAP_SECONDS)

    # 스페이스 권한 변경: 스페이스의 문서마다 이벤트가 한꺼번에 나간다
    for viewers in (BURST_VIEWERS + ["group:product"], BURST_VIEWERS):
        before = lab.wiki_revisions({d.doc_id for d in FileWikiSource(settings.wiki_dir).documents()
                                     if d.space == BURST_SPACE})
        started = time.perf_counter()
        lab.call("PUT", f"/admin/spaces/{BURST_SPACE}/viewers", json={"principals": viewers})
        expected = {doc_id: rev + 1 for doc_id, rev in before.items()}
        for doc_id, seconds in lab.wait(expected, started).items():
            records.append({"scenario": "space_burst", "doc_id": doc_id, "revision": expected[doc_id],
                            "seconds": round(seconds, 3)})
        print("space_burst", summary([r["seconds"] for r in records if r["scenario"] == "space_burst"]))
        time.sleep(GAP_SECONDS)

    out = Path(__file__).parent / "results" / "latency.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    table = {s: summary([r["seconds"] for r in records if r["scenario"] == s])
             for s in dict.fromkeys(r["scenario"] for r in records)}
    table["all"] = summary([r["seconds"] for r in records])
    (out.parent / "latency_summary.json").write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8")
    for scenario, row in table.items():
        print(scenario, row)


if __name__ == "__main__":
    main()
