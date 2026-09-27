"""Ko-StrategyQA로 임베딩 모델의 한국어 검색 성능과 속도를 잰다. 모델 하나를 인자로 받아 결과 한 줄을 출력한다.

    uv run --python 3.12 --with sentence-transformers --with torch --with datasets python ko_retrieval.py BAAI/bge-m3

- 데이터: mteb/Ko-StrategyQA dev (문서 9,251개, 질문 592개)
- Recall@5: 상위 5개 안에 정답 문서가 하나라도 있으면 1 (설계서 ADR-20과 같은 정의)
- nDCG@10: 등급이 있는 정답 기준, MTEB의 주 지표
- 문서 임베딩은 Mac GPU(MPS), 질문 지연은 GPU 없는 배포 서버를 가정해 CPU에서 한 개씩 잰다
"""

import json
import math
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

CACHE = Path(__file__).with_name(".cache") / "kostrategyqa.json"
# 모델 설정에 질문·문서 프롬프트가 없어 직접 붙여야 하는 모델
PREFIX = {"intfloat/multilingual-e5-large": ("query: ", "passage: "),
          "intfloat/multilingual-e5-base": ("query: ", "passage: ")}


def load_data():
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    from datasets import load_dataset
    name = "mteb/Ko-StrategyQA"
    first = lambda ds: ds[list(ds.keys())[0]]  # 세 구성 모두 dev 분할 하나뿐이다
    corpus = first(load_dataset(name, "corpus"))
    queries = first(load_dataset(name, "queries"))
    qrels = first(load_dataset(name, "qrels"))
    docs = {row["_id"]: ((row.get("title") or "") + "\n" + row["text"]).strip() for row in corpus}
    rel = {}
    for row in qrels:
        if row["score"] > 0:
            rel.setdefault(str(row["query-id"]), {})[str(row["corpus-id"])] = row["score"]
    qs = {row["_id"]: row["text"] for row in queries if row["_id"] in rel}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps([docs, qs, rel], ensure_ascii=False), encoding="utf-8")
    return docs, qs, rel


def encode(model, name, texts, kind, batch_size):
    if name in PREFIX:
        prefix = PREFIX[name][0 if kind == "query" else 1]
        return model.encode([prefix + t for t in texts], batch_size=batch_size, normalize_embeddings=True)
    fn = model.encode_query if kind == "query" else model.encode_document  # 모델 설정의 프롬프트를 적용
    return fn(texts, batch_size=batch_size, normalize_embeddings=True)


def main(name):
    docs, qs, rel = load_data()
    doc_ids, doc_texts = list(docs), list(docs.values())
    query_ids, query_texts = list(qs), list(qs.values())

    model = SentenceTransformer(name, device="mps")
    model.max_seq_length = 512
    t0 = time.perf_counter()
    doc_vecs = encode(model, name, doc_texts, "document", 32)
    torch.mps.synchronize()
    corpus_s = time.perf_counter() - t0
    query_vecs = encode(model, name, query_texts, "query", 32)
    top = np.argsort(-(query_vecs @ doc_vecs.T), axis=1)[:, :10]

    hit5, ndcg = [], []
    for i, qid in enumerate(query_ids):
        judged = rel[qid]
        ranked = [doc_ids[j] for j in top[i]]
        hit5.append(any(d in judged for d in ranked[:5]))
        dcg = sum(judged.get(d, 0) / math.log2(k + 2) for k, d in enumerate(ranked))
        ideal = sorted(judged.values(), reverse=True)[:10]
        ndcg.append(dcg / sum(v / math.log2(k + 2) for k, v in enumerate(ideal)))
    del model
    torch.mps.empty_cache()

    cpu = SentenceTransformer(name, device="cpu")
    cpu.max_seq_length = 512
    sample = query_texts[:70]
    for text in sample[:6]:  # 워밍업
        encode(cpu, name, [text], "query", 1)
    lat = []
    for text in sample[6:]:
        t0 = time.perf_counter()
        encode(cpu, name, [text], "query", 1)
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()

    result = {"model": name, "queries": len(query_ids), "docs": len(doc_ids),
              "recall5": float(np.mean(hit5)), "ndcg10": float(np.mean(ndcg)),
              "corpus_min_mps": corpus_s / 60, "docs_per_s_mps": len(doc_ids) / corpus_s,
              "cpu_q_p50_ms": statistics.median(lat), "cpu_q_p95_ms": lat[int(len(lat) * 0.95) - 1],
              "dim": int(doc_vecs.shape[1])}
    print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
