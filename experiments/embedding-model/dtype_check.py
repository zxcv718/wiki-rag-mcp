"""Qwen3-Embedding-0.6B가 느린 원인 중 가중치 형식(bfloat16)의 몫을 잰다.

모델 파일은 bfloat16인데 최신 transformers는 파일에 적힌 형식 그대로 불러온다.
M1 GPU는 bfloat16을 하드웨어로 계산하지 않으므로 float32와 속도를 비교한다.

    uv run --python 3.12 --with sentence-transformers --with torch --with datasets python dtype_check.py
"""

import statistics
import time

import torch
from sentence_transformers import SentenceTransformer

from ko_retrieval import load_data

NAME = "Qwen/Qwen3-Embedding-0.6B"


def main():
    docs, qs, _ = load_data()
    doc_texts, query_texts = list(docs.values())[:256], list(qs.values())[:36]
    vectors = {}
    for device in ("mps", "cpu"):
        for dtype in (torch.bfloat16, torch.float32):
            model = SentenceTransformer(NAME, device=device, model_kwargs={"dtype": dtype})
            model.max_seq_length = 512
            label = f"{device}/{str(dtype).split('.')[-1]}"
            if device == "mps":
                model.encode_document(doc_texts[:32], batch_size=32)  # 워밍업
                t0 = time.perf_counter()
                vectors[label] = model.encode_document(doc_texts, batch_size=32, normalize_embeddings=True)
                torch.mps.synchronize()
                print(f"{label}: 문서 256개 {time.perf_counter() - t0:.1f}s", flush=True)
            else:
                for text in query_texts[:6]:
                    model.encode_query([text])
                lat = []
                for text in query_texts[6:]:
                    t0 = time.perf_counter()
                    model.encode_query([text])
                    lat.append((time.perf_counter() - t0) * 1000)
                print(f"{label}: 질문 1개 p50 {statistics.median(lat):.0f}ms", flush=True)
            del model
            torch.mps.empty_cache()
    cos = (vectors["mps/bfloat16"] * vectors["mps/float32"]).sum(axis=1).mean()
    print(f"bfloat16과 float32 벡터의 코사인 평균 {cos:.4f}")


if __name__ == "__main__":
    main()
