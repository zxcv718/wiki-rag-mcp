"""판정 실험 실행 (7장 실험 기록 표). 구성과 측정 방법은 첫 실험 전에 이 파일로 고정한다.

- 모든 구성은 권한 pre-filter를 켠 채 문항의 질문자로 검색한다 (ADR-20).
- 질문 임베딩과 리랭커는 CPU에서 돌린다. 배포 서버에 GPU가 없고, ADR-20이 장비 사양(CPU, 메모리)을 적게 한다.
- 동의어 확장은 없다 (ADR-20). 가상 위키를 만든 약어 사전으로 확장하면 정답을 미리 아는 셈이다.
- 리랭커 구성은 첫 10문항으로 워밍업한 뒤 전체를 3회 순차 실행해 리랭킹 단계 p95를 잰다 (ADR-20).
  품질 지표는 첫 회 결과로 계산한다.
"""

import json
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wiki_rag_mcp.search.reranker import CANDIDATES
from wiki_rag_mcp.search.store import OpenSearchStore

TOP = 10  # MRR@10, nDCG@10까지 보려면 10개가 필요하다
WARMUP = 10
RERANK_PASSES = 3


@dataclass(frozen=True)
class Index:
    alias: str
    embedder: str  # "bge-m3" 또는 "gemini"
    with_header: bool


INDEXES = {
    "main": Index("wiki-chunks", "bge-m3", True),  # 서버가 쓰는 인덱스
    "noheader": Index("wiki-eval-noheader", "bge-m3", False),
    "gemini": Index("wiki-eval-gemini", "gemini", True),
}


@dataclass(frozen=True)
class Config:
    index: str
    mode: str  # "vector" 또는 "hybrid"
    rerank: bool = False


# 7장 실험 기록 표의 행. 리랭커와 상용 임베딩 행은 앞 판정 결과에 따라 vector 또는 hybrid 쪽을 쓴다
CONFIGS = {
    "baseline": Config("main", "vector"),
    "no-header": Config("noheader", "vector"),
    "hybrid": Config("main", "hybrid"),
    "no-header-hybrid": Config("noheader", "hybrid"),
    "vector-rerank": Config("main", "vector", rerank=True),
    "hybrid-rerank": Config("main", "hybrid", rerank=True),
    "gemini-vector": Config("gemini", "vector"),
    "gemini-hybrid": Config("gemini", "hybrid"),
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
    def __init__(self, config: Config, client, source, embedder, reranker=None):
        if config.rerank and reranker is None:
            raise ValueError("리랭커 구성에는 reranker가 필요하다")
        self.config = config
        self.store = OpenSearchStore(client, INDEXES[config.index].alias)
        self.source = source
        self.embedder = embedder
        self.reranker = reranker

    def principals(self, user: str) -> list[str]:
        return [f"user:{user}", *self.source.groups_of(user)]

    def search(self, question: str, user: str) -> tuple[list[dict[str, Any]], dict[str, float]]:
        principals = self.principals(user)
        started = time.perf_counter()
        vector = self.embedder.encode_query(question)
        embedded = time.perf_counter()
        depth = CANDIDATES if self.config.rerank else TOP
        if self.config.mode == "hybrid":
            hits = self.store.hybrid_search(vector, question, principals, depth)
        else:
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
    rows = []
    for n in range(passes):
        for i, q in enumerate(golden):
            hits, timings = runner.search(q["question"], q["asker"])
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
            "embedder": getattr(runner.embedder, "model_name", ""),
            "embedder_revision": getattr(runner.embedder, "revision", ""),
            "reranker": getattr(runner.reranker, "model_name", None),
            "reranker_revision": getattr(runner.reranker, "revision", None),
            "device": getattr(runner.embedder, "device", "api"), "passes": passes, "warmup": WARMUP,
            "questions": len(golden), "git": git_head(), "machine": machine(),
            "at": datetime.now(UTC).isoformat(timespec="seconds")}
    (out_dir / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
