"""PyTorch(sentence-transformers)로 임베딩 속도를 Mac GPU(MPS)와 CPU에서 재고, 한국어 확인용 시험을 돌린다.

    uv run --python 3.12 --with sentence-transformers --with torch python speed.py [모델 이름]
"""

import gc
import resource
import statistics
import sys
import time

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from common import QUERIES, TOY_DOCS, TOY_TESTS, make_chunks


def sync(device):
    if device == "mps":
        torch.mps.synchronize()


def bench(name, device):
    t0 = time.perf_counter()
    model = SentenceTransformer(name, device=device)
    load = time.perf_counter() - t0
    chunks = make_chunks(model.tokenizer)
    tokens = np.mean([len(model.tokenizer(c)["input_ids"]) for c in chunks])
    model.encode(chunks[:8], batch_size=8, normalize_embeddings=True)  # 워밍업
    t0 = time.perf_counter()
    model.encode(chunks, batch_size=16, normalize_embeddings=True)
    sync(device)
    elapsed = time.perf_counter() - t0
    for q in QUERIES[:5]:
        model.encode([q], normalize_embeddings=True)
    lat = []
    for q in QUERIES:
        t0 = time.perf_counter()
        model.encode([q], normalize_embeddings=True)
        sync(device)
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    rate = len(chunks) / elapsed
    print(f"[{device}] 모델 로드 {load:.1f}s | 청크 {len(chunks)}개(평균 {tokens:.0f}토큰) {elapsed:.1f}s = {rate:.1f}개/s, "
          f"3,000개 환산 {3000 / rate / 60:.1f}분 | 질문 1개 p50 {statistics.median(lat):.0f}ms p95 {lat[int(len(lat) * 0.95) - 1]:.0f}ms")
    return model


def toy_quality(model):
    names = list(TOY_DOCS)
    docs = model.encode([f"{k}\n{v}" for k, v in TOY_DOCS.items()], normalize_embeddings=True)
    correct = 0
    for query, want in TOY_TESTS:
        scores = docs @ model.encode([query], normalize_embeddings=True)[0]
        top = names[int(np.argmax(scores))]
        correct += top == want
        print(f"  {'맞음' if top == want else '틀림'} | {query} | 1위: {top} ({scores.max():.2f})")
    print(f"  확인용 시험 {correct}/{len(TOY_TESTS)} 정답")


def main(name):
    print(f"{name} | torch {torch.__version__}, MPS 사용 가능: {torch.backends.mps.is_available()}")
    model = bench(name, "mps")
    toy_quality(model)
    del model
    gc.collect()
    torch.mps.empty_cache()
    bench(name, "cpu")
    print(f"최대 메모리(RSS) {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3:.1f}GB")  # macOS는 바이트 단위


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-m3")
