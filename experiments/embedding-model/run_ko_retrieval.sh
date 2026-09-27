#!/bin/sh
# 후보 모델을 하나씩 별도 프로세스로 재서 results/ko_retrieval.jsonl에 한 줄씩 쌓는다.
cd "$(dirname "$0")"
export HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false
mkdir -p results
for m in BAAI/bge-m3 nlpai-lab/KURE-v1 dragonkue/BGE-m3-ko Snowflake/snowflake-arctic-embed-l-v2.0 \
         intfloat/multilingual-e5-large intfloat/multilingual-e5-base Qwen/Qwen3-Embedding-0.6B; do
  echo "== $m $(date +%H:%M:%S)"
  uv run --quiet --python 3.12 --with sentence-transformers --with torch --with datasets \
    python ko_retrieval.py "$m" 2>&1 | grep "^RESULT " | sed 's/^RESULT //' | tee -a results/ko_retrieval.jsonl
done
