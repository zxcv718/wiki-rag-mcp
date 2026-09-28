"""쿼리 임베딩 속도 벤치마크 (README.md "방법" 1). 운영 서버의 검색 이미지 안에서 돈다.

float32와 bf16 모델을 차례로 불러(메모리를 아끼려고 동시에 올리지 않는다) 아래를 잰다.
- 쿼리 하나씩의 지연: 워밍업 10회 뒤 골든셋 질문 300개를 차례로
- 묶음 크기별 처리량: 질문 128개를 묶음 크기(1, 2, 4, 8, 16, 32)로 나눠 인코딩
- 코사인 유사도: 32개씩 묶어 만든 벡터와 하나씩 만든 벡터, bf16과 float32 벡터

    python bench.py <golden.jsonl> > bench.json
"""

import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from wiki_rag_mcp.config import EMBEDDING_MODEL, EMBEDDING_REVISION

BATCH_SIZES = (1, 2, 4, 8, 16, 32)
BATCH_QUESTIONS = 128


def encode(model: SentenceTransformer, texts: list[str]) -> np.ndarray:
    return model.encode(texts, batch_size=len(texts), normalize_embeddings=True, show_progress_bar=False)


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(values, q))


def measure(dtype: torch.dtype, questions: list[str]) -> tuple[dict, np.ndarray]:
    model = SentenceTransformer(EMBEDDING_MODEL, revision=EMBEDDING_REVISION, device="cpu",
                                model_kwargs={"dtype": dtype})
    for q in questions[:10]:
        encode(model, [q])
    single, vectors = [], []
    for q in questions:
        start = time.perf_counter()
        vectors.append(encode(model, [q])[0])
        single.append((time.perf_counter() - start) * 1000)
    vectors = np.array(vectors)
    batches, batched = {}, None
    sample = questions[:BATCH_QUESTIONS]
    for size in BATCH_SIZES:
        start = time.perf_counter()
        out = [encode(model, sample[i:i + size]) for i in range(0, len(sample), size)]
        batches[str(size)] = round(len(sample) / (time.perf_counter() - start), 2)
        if size == BATCH_SIZES[-1]:
            batched = np.concatenate(out)
    cos = (batched * vectors[:BATCH_QUESTIONS]).sum(axis=1)
    stats = {"single_ms": {"p50": round(percentile(single, 50), 1), "p95": round(percentile(single, 95), 1),
                           "mean": round(statistics.fmean(single), 1)},
             "per_second_by_batch": batches,
             "batch32_vs_single_cos_min": float(cos.min())}
    return stats, vectors


def main() -> None:
    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    questions = [json.loads(line)["question"] for line in lines]
    result = {"machine": platform.processor() or platform.machine(), "torch": torch.__version__,
              "threads": torch.get_num_threads(), "questions": len(questions)}
    vectors = {}
    for name, dtype in (("float32", torch.float32), ("bfloat16", torch.bfloat16)):
        result[name], vectors[name] = measure(dtype, questions)
        print(name, "끝", file=sys.stderr)
    cos = (vectors["float32"] * vectors["bfloat16"].astype(np.float32)).sum(axis=1)
    result["bf16_vs_float32_cos"] = {"min": float(cos.min()), "mean": float(cos.mean())}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
