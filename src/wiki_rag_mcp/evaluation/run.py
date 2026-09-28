"""판정 실험 실행 (7장 실험 기록 표). 구성과 측정 방법은 실험 전에 이 파일로 고정한다.

M2 판정(하이브리드, 상용 임베딩 비교 포함)의 코드는 태그 m2-judgement에 있다. 판정 뒤에는 서버가 쓰는
pgvector 구성만 남겼다.

- 모든 구성은 권한 pre-filter를 켠 채 문항의 질문자로 검색한다 (ADR-20).
- 질문 임베딩과 리랭커는 CPU에서 돌린다. 배포 서버에 GPU가 없고, ADR-20이 장비 사양(CPU, 메모리)을 적게 한다.
- 동의어 확장은 없다 (ADR-20). 가상 위키를 만든 약어 사전으로 확장하면 정답을 미리 아는 셈이다.
- 리랭커 구성은 첫 10문항으로 워밍업한 뒤 전체를 3회 순차 실행해 리랭킹 단계 p95를 잰다 (ADR-20).
  품질 지표는 첫 회 결과로 계산한다.
"""

import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wiki_rag_mcp.search.reranker import CANDIDATES

TOP = 10  # MRR@10, nDCG@10까지 보려면 10개가 필요하다
WARMUP = 10
RERANK_PASSES = 3


@dataclass(frozen=True)
class Index:
    alias: str
    with_header: bool


INDEXES = {
    "main": Index("wiki-chunks", True),  # 서버가 쓰는 인덱스
    "noheader": Index("wiki-eval-noheader", False),
}


@dataclass(frozen=True)
class Config:
    index: str
    rerank: bool = False
    query_dtype: str = "float32"  # 쿼리 인코딩 형식. 문서 벡터는 인덱스에 든 그대로다
    query_batch: int = 1  # 1보다 크면 골든셋 질문을 이만큼씩 묶어 미리 인코딩한다 (묶음 처리를 켠 서버가 몰릴 때)


CONFIGS = {
    "baseline": Config("main"),
    "no-header": Config("noheader"),
    "vector-rerank": Config("main", rerank=True),
    # ADR-11 재판정 (experiments/m5-speedup): 쿼리만 bf16, 하나씩과 16개씩 묶어
    "query-bf16": Config("main", query_dtype="bfloat16"),
    "query-bf16-batch16": Config("main", query_dtype="bfloat16", query_batch=16),
}


def machine() -> dict[str, Any]:
    def sysctl(name: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    mem = sysctl("hw.memsize")
    return {"cpu": sysctl("machdep.cpu.brand_string") or platform.processor(), "cores": sysctl("hw.ncpu"),
            "memory_gb": round(int(mem) / 1024**3) if mem else None, "os": platform.platform()}


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


class Runner:
    def __init__(self, config: Config, store, source, embedder, reranker=None):
        if config.rerank and reranker is None:
            raise ValueError("리랭커 구성에는 reranker가 필요하다")
        self.config = config
        self.store = store
        self.source = source
        self.embedder = embedder
        self.reranker = reranker

    def principals(self, user: str) -> list[str]:
        return [f"user:{user}", *self.source.groups_of(user)]

    def search(self, question: str, user: str,
               vector: list[float] | None = None) -> tuple[list[dict[str, Any]], dict[str, float]]:
        principals = self.principals(user)
        started = time.perf_counter()
        if vector is None:
            vector = self.embedder.encode_query(question)
        embedded = time.perf_counter()
        depth = CANDIDATES if self.config.rerank else TOP
        hits = self.store.knn_search(vector, principals, depth)
        searched = time.perf_counter()
        if self.reranker is not None:
            hits = self.reranker.rerank(question, hits)
        done = time.perf_counter()
        timings = {"embed_ms": (embedded - started) * 1000, "search_ms": (searched - embedded) * 1000,
                   "rerank_ms": (done - searched) * 1000 if self.config.rerank else 0.0,
                   "total_ms": (done - started) * 1000}
        return hits[:TOP], timings


def run(name: str, runner: Runner, golden: list[dict[str, Any]], out_dir: Path) -> Path:
    passes = RERANK_PASSES if runner.config.rerank else 1
    for q in golden[:WARMUP]:
        runner.search(q["question"], q["asker"])
    vectors: list[Any] = [None] * len(golden)
    if (size := runner.config.query_batch) > 1:  # 묶어 만든 벡터로 검색한다. 이때 embed_ms는 0에 가깝다
        questions = [q["question"] for q in golden]
        vectors = [v.tolist() for i in range(0, len(questions), size)
                   for v in runner.embedder.encode_queries(questions[i:i + size])]
    rows = []
    for n in range(passes):
        for i, q in enumerate(golden):
            if i % 50 == 0:
                print(f"  {n + 1}/{passes}회차 {i}/{len(golden)}문항", file=sys.stderr, flush=True)
            hits, timings = runner.search(q["question"], q["asker"], vectors[i])
            if n == 0:
                rows.append({"id": q["id"], "type": q["type"], "relevant": q["relevant"],
                             "results": [{"chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "score": h["score"]}
                                         for h in hits], "timings": [timings]})
            else:
                rows[i]["timings"].append(timings)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    meta = {"config": name, **asdict(runner.config), "index": asdict(INDEXES[runner.config.index]),
            "store": type(runner.store).__name__,
            "embedder": getattr(runner.embedder, "model_name", ""),
            "embedder_revision": getattr(runner.embedder, "revision", ""),
            "reranker": getattr(runner.reranker, "model_name", None),
            "reranker_revision": getattr(runner.reranker, "revision", None),
            "device": getattr(runner.embedder, "device", "api"),
            "embedder_dtype": getattr(runner.embedder, "dtype", None), "passes": passes, "warmup": WARMUP,
            "questions": len(golden), "git": git_head(), "machine": machine(),
            "at": datetime.now(UTC).isoformat(timespec="seconds")}
    (out_dir / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
