# 임베딩 모델 선택 실험 (2026-09-27)

MCP 서버 안에서 직접 돌릴 다국어 임베딩 모델(ADR-11)을 고르기 위한 측정입니다. 상용 임베딩 API와 비교해 ADR-11을 판정하는 실험은 M2에서 골든셋으로 따로 합니다. 이 기록은 그 전에 "서버에 넣을 후보 중 무엇을 기본값으로 쓸지"만 정합니다.

## 결론

- **기본값은 `BAAI/bge-m3`를 제안합니다.** 한국어 검색 품질은 상위 5개 모델이 사실상 같았고, 그중 입력 길이, 라이선스, 쓰기 편함, 생태계를 따져 골랐습니다.
- **예비안은 `intfloat/multilingual-e5-base`입니다.** Recall@5가 2%p쯤 낮지만 CPU에서 질문 하나를 3배 빨리 처리하고 메모리는 절반입니다. 배포 서버에서 지연 예산(질문 임베딩 100ms)을 지키지 못하면 이쪽으로 바꿉니다.

## 환경

| 항목 | 값 |
|---|---|
| 장비 | MacBook Pro, Apple M1 Pro (CPU 10코어, GPU 16코어), 메모리 16GB |
| 운영체제 | macOS 27.0 |
| 라이브러리 | Python 3.12.12, sentence-transformers 6.1.0, torch 2.14.0, transformers 5.17.0, datasets 5.0.1 |

## 방법

- **데이터**: [Ko-StrategyQA](https://huggingface.co/datasets/mteb/Ko-StrategyQA) dev. MTEB의 한국어 검색 과제로, 문서 9,251개(평균 320자)와 질문 592개입니다.
- **지표**: Recall@5는 상위 5개 안에 정답 문서가 하나라도 있으면 1입니다(설계서 ADR-20과 같은 정의). nDCG@10은 MTEB의 주 지표입니다.
- **검색**: 문서는 최대 512토큰으로 자르고, 정규화한 벡터의 내적으로 순위를 매깁니다. 질문과 문서의 프롬프트는 모델 설정을 따르고, 설정에 프롬프트가 없는 e5는 `query: `와 `passage: `를 직접 붙입니다.
- **속도**: 문서 전체 임베딩은 Mac GPU(MPS)에서 배치 32로 잽니다. 질문 지연은 GPU 없는 배포 서버를 가정해 CPU에서 한 개씩 64번 재고, 앞의 워밍업 6번은 뺍니다.
- **가중치 형식**: 모델 파일에 적힌 형식 그대로 불러옵니다. Qwen3만 bfloat16이고 나머지는 float32입니다.

## 후보

| 모델 | 파라미터 | 최대 입력 | 라이선스 | 월 다운로드 | 측정 |
|---|---|---|---|---|---|
| BAAI/bge-m3 | 568M | 8,192토큰 | MIT | 약 3,660만 | 함 |
| nlpai-lab/KURE-v1 (bge-m3 한국어 추가 학습) | 568M | 8,192토큰 | MIT | 약 32만 | 함 |
| dragonkue/BGE-m3-ko (bge-m3 한국어 추가 학습) | 568M | 8,192토큰 | Apache-2.0 | 약 6만 | 함 |
| Snowflake/snowflake-arctic-embed-l-v2.0 | 568M | 8,192토큰 | Apache-2.0 | 약 94만 | 함 |
| intfloat/multilingual-e5-large | 560M | 512토큰 | MIT | 약 740만 | 함 |
| intfloat/multilingual-e5-base | 278M | 512토큰 | MIT | 약 750만 | 함 |
| Qwen/Qwen3-Embedding-0.6B | 596M | 32,768토큰 | Apache-2.0 | 약 930만 | 함 |
| jinaai/jina-embeddings-v3 | 572M | | CC BY-NC 4.0 | | 비상업 라이선스라 제외 |
| google/embeddinggemma-300m | 303M | | Gemma 약관 | | 약관 승인을 거쳐야 받을 수 있어 제외 |
| Alibaba-NLP/gte-multilingual-base | 305M | 8,192토큰 | Apache-2.0 | | 모델 저장소의 코드를 실행해야 해서(trust_remote_code) 제외 |

파라미터, 라이선스, 다운로드 수는 2026-09-27에 Hugging Face API로, 최대 입력은 각 모델의 `config.json`으로 확인했습니다.

## 결과

원본 수치는 [results/ko_retrieval.jsonl](results/ko_retrieval.jsonl)에 있습니다.

| 모델 | Recall@5 | nDCG@10 | 문서 9,251개 임베딩 (Mac GPU) | 질문 1개, CPU p50 / p95 | 차원 | 가중치 크기 (float32) |
|---|---|---|---|---|---|---|
| bge-m3 | 0.883 | 0.794 | 5.2분 | 104 / 110ms | 1024 | 약 2.3GB |
| KURE-v1 | 0.883 | 0.799 | 5.2분 | 113 / 141ms | 1024 | 약 2.3GB |
| BGE-m3-ko | 0.882 | 0.795 | 5.3분 | 112 / 117ms | 1024 | 약 2.3GB |
| snowflake-arctic-embed-l-v2.0 | 0.883 | 0.804 | 5.3분 | 116 / 148ms | 1024 | 약 2.3GB |
| multilingual-e5-large | 0.882 | 0.803 | 5.4분 | 109 / 119ms | 1024 | 약 2.2GB |
| multilingual-e5-base | 0.860 | 0.764 | 1.7분 | 33 / 35ms | 768 | 약 1.1GB |
| Qwen3-Embedding-0.6B | 0.873 | 0.769 | 20.8분 | 230 / 272ms | 1024 | 약 2.4GB |

## 해석

- **상위 5개는 품질로 가를 수 없습니다.** Recall@5는 0.882~0.883, nDCG@10은 0.794~0.804입니다. 질문 592개에서 Recall@5의 95% 신뢰구간은 모델 하나만 봐도 ±2.6%p라, 1%p 안의 차이는 우연으로도 생깁니다.
- **한국어로 추가 학습한 모델(KURE-v1, BGE-m3-ko)이 원본 bge-m3보다 뚜렷이 낫지 않았습니다.** 이 데이터에서는 추가 학습의 이득이 보이지 않았습니다. 사내 위키 문서에서는 다를 수 있어 M2 골든셋에서 다시 볼 만합니다. 두 모델은 bge-m3와 구조와 차원이 같아서, 재색인만 하면 코드 변경 없이 바꿀 수 있습니다.
- **multilingual-e5-base는 품질을 조금 내주고 속도를 크게 얻습니다.** Recall@5가 2.3%p, nDCG@10이 3%p쯤 낮지만 CPU 지연은 3분의 1입니다.
- **Qwen3-Embedding-0.6B는 품질이 상위권보다 낮고 가장 느렸습니다.** 느린 원인은 아래 "Qwen3가 느린 이유"에 적었습니다.

## bge-m3를 기본값으로 고른 이유

품질이 같은 상위 5개 중에서 다음을 비교했습니다.

- **입력 길이**: e5-large는 최대 512토큰입니다. 청크가 300~500토큰이고 앞에 맥락 헤더가 붙어서(6장) 여유가 없습니다. bge-m3, KURE, arctic은 8,192토큰입니다.
- **쓰기 편함**: bge-m3와 KURE는 질문과 문서에 프롬프트를 붙이지 않아도 됩니다. e5는 `query: `, `passage: `를 빠뜨리면 품질이 떨어지는데, 이런 실수는 테스트로 잘 잡히지 않습니다.
- **생태계**: bge-m3는 월 다운로드가 3,600만 회를 넘어 후보 중 가장 많습니다. MLX와 ONNX 변환본, 같은 계열의 리랭커(bge-reranker-v2-m3)가 공개되어 있습니다.
- **바꾸기 쉬움**: 나중에 KURE-v1이나 BGE-m3-ko가 골든셋에서 낫게 나오면, 같은 구조라 재색인만으로 바꿀 수 있습니다.

snowflake-arctic-embed-l-v2.0도 조건이 비슷하지만(8,192토큰, Apache-2.0), 질문에 프롬프트를 붙여야 하고 이 측정에서 CPU p95가 가장 높았습니다.

## 남은 문제: CPU 지연

bge-m3의 CPU 질문 지연 p95는 110ms로 설계서의 예산(100ms, 8장)을 조금 넘습니다. 같은 스크립트를 다시 돌렸을 때는 137ms까지 나와, 속도는 실행할 때마다 흔들렸습니다([results/speed_bge_m3.txt](results/speed_bge_m3.txt)). 측정하는 동안 Mac의 스왑이 3.4GB 쓰이고 있었습니다. Mac CPU는 배포 서버 CPU와도 다르므로 M5에서 후보 서버로 다시 재고, 예산을 넘으면 multilingual-e5-base나 양자화를 검토합니다.

## Qwen3가 느린 이유

Qwen3는 다른 모델보다 문서 임베딩에 4배 가까이 걸렸습니다. [dtype_check.py](dtype_check.py)로 원인을 나눠 봤습니다([results/qwen3_dtype.txt](results/qwen3_dtype.txt)).

| 원인 | 확인 결과 |
|---|---|
| 가중치 형식 | 모델 파일이 bfloat16이고, M1 GPU는 bfloat16을 하드웨어로 계산하지 않습니다. float32로 바꾸면 Mac GPU에서 23%, CPU에서 43% 빨라집니다. 벡터는 같습니다(코사인 1.000). |
| 토큰 수 | 같은 한국어 문서를 Qwen3 토크나이저는 26% 더 많은 토큰으로 자릅니다(평균 172개 대 216개). |
| 층 수 | 28층으로, bge-m3의 24층보다 많습니다. |

세 가지를 합쳐도 2배 정도라 나머지 원인은 찾지 못했습니다. Qwen3는 float32로 바꿔도 CPU p50이 169ms로 bge-m3보다 느리고 품질도 낮아서, 원인을 더 찾지 않았습니다.

**배울 점**: 최신 transformers는 모델 파일에 적힌 형식 그대로 불러옵니다. 모델을 정할 때 이름과 버전뿐 아니라 불러올 형식도 설정으로 고정해야 합니다(ADR-11의 "모델 이름과 버전 기록"에 형식도 포함).

## MLX 참고

MLX는 Apple Silicon 전용이라 Linux 배포 서버에서는 쓸 수 없습니다. 개발용 가속으로만 의미가 있어 스크립트는 저장소에 두지 않았고, 결과만 [results/speed_mlx.txt](results/speed_mlx.txt)에 남깁니다.

- bge-m3를 MLX(fp16)로 돌리면 질문 1개가 24ms로 PyTorch Mac GPU(37ms)보다 빨랐습니다. 반면 청크를 한꺼번에 임베딩할 때는 오히려 조금 느렸습니다.
- **주의**: mlx-embeddings의 기본 출력(`text_embeds`)은 평균 방식(mean pooling)이라, CLS 방식을 쓰는 bge-m3의 공식 벡터와 코사인 0.73~0.77로 달랐습니다. CLS 벡터를 직접 꺼내야 PyTorch와 같아집니다(코사인 1.000). 모르고 쓰면 검색 품질이 조용히 떨어집니다.

## 한계

- Ko-StrategyQA는 위키백과 계열 질문을 한국어로 옮긴 데이터라 사내 위키와 다릅니다. 모델의 최종 판단은 M2 골든셋으로 합니다.
- 한국어로 추가 학습한 모델이 이 벤치마크와 비슷한 데이터로 학습했을 가능성은 배제하지 못했습니다.
- 모델마다 한 번씩 쟀습니다. 품질 수치는 다시 돌려도 같았지만(e5-base로 확인, [results/repro_e5_base.txt](results/repro_e5_base.txt)), 속도는 실행마다 흔들립니다.
- 측정에 쓴 모델과 데이터셋은 기록을 남긴 뒤 이 Mac에서 지웠습니다. 다시 돌리면 새로 받습니다.

## 다시 돌리는 방법

```bash
cd experiments/embedding-model
./run_ko_retrieval.sh                      # 후보 7개의 한국어 검색 성능과 속도 (40분 안팎)
uv run --python 3.12 --with sentence-transformers --with torch python speed.py BAAI/bge-m3
uv run --python 3.12 --with sentence-transformers --with torch --with datasets python dtype_check.py
```
