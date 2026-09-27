"""실행 결과를 지표로 요약하고 두 구성을 짝 비교한다."""

import json
from pathlib import Path
from typing import Any

import numpy as np

from wiki_rag_mcp.evaluation.judge import Comparison, compare
from wiki_rag_mcp.evaluation.metrics import METRICS


def load_run(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def per_question(rows: list[dict[str, Any]], metric: str) -> dict[str, float]:
    """정답이 있는 문항(270개)만 센다. 답 없음 문항은 지표에서 뺀다 (7장)."""
    fn = METRICS[metric]
    return {r["id"]: fn([h["doc_id"] for h in r["results"]], set(r["relevant"]))
            for r in rows if r["relevant"]}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {m: float(np.mean(list(per_question(rows, m).values()))) for m in METRICS}
    out["n"] = len(per_question(rows, "recall@5"))
    totals = [t["total_ms"] for r in rows for t in r["timings"]]
    reranks = [t["rerank_ms"] for r in rows for t in r["timings"]]
    out["p50_ms"], out["p95_ms"] = (float(x) for x in np.percentile(totals, [50, 95]))
    out["rerank_p95_ms"] = float(np.percentile(reranks, 95)) if any(reranks) else None
    by_type: dict[str, list[float]] = {}
    recall = per_question(rows, "recall@5")
    for r in rows:
        if r["id"] in recall:
            by_type.setdefault(r["type"], []).append(recall[r["id"]])
    out["recall@5_by_type"] = {t: float(np.mean(v)) for t, v in sorted(by_type.items())}  # 기록만 (ADR-20)
    top = {r["type"] == "no_answer": [] for r in rows}
    for r in rows:
        if r["results"]:
            top[r["type"] == "no_answer"].append(r["results"][0]["score"])
    out["top1_score_median"] = {"답 없음" if k else "정답 있음": float(np.median(v)) for k, v in top.items() if v}
    return out


def paired(base: list[dict[str, Any]], candidate: list[dict[str, Any]], metric: str) -> Comparison:
    a, b = per_question(base, metric), per_question(candidate, metric)
    if a.keys() != b.keys():
        raise ValueError("두 실행의 문항이 다르다. 같은 골든셋으로 돌렸는지 확인한다")
    ids = sorted(a)
    return compare([a[i] for i in ids], [b[i] for i in ids])

