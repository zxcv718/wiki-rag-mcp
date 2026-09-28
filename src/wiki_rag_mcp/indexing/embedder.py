"""MCP 서버 안에서 직접 돌리는 임베딩 모델 (ADR-11).

모델 이름, 커밋, 형식(float32)을 고정해서 불러온다. 최신 transformers는 모델 파일에 적힌 형식 그대로
불러오므로 형식을 지정하지 않으면 장비에 따라 속도가 크게 달라진다(experiments/embedding-model).
bge-m3의 문장 벡터는 CLS 토큰 벡터이고, sentence-transformers 설정이 이를 따른다.
"""

import os
import queue
import threading
from concurrent.futures import Future

import numpy as np
from opentelemetry import metrics, trace

from wiki_rag_mcp.config import EMBEDDING_DTYPE, EMBEDDING_MODEL, EMBEDDING_REVISION

MAX_SEQ_LENGTH = 2048  # 청크는 500토큰 안팎이지만 자르지 않는 큰 표가 있어 여유를 둔다

_tracer = trace.get_tracer(__name__)
# 임베딩 호출 수(ADR-15). 문서는 한 번에 여러 청크를 넣으므로 호출 대신 텍스트 수로 센다. 쿼리는 호출 하나가 하나다
_texts = metrics.get_meter(__name__).create_counter(
    "wiki.embedding.texts", unit="{text}", description="임베딩한 텍스트 수. kind: query(검색 쿼리), document(청크)")
_batch_sizes = metrics.get_meter(__name__).create_histogram(
    "wiki.embedding.query_batch.size", unit="{query}", description="한 번에 묶어 인코딩한 쿼리 수 (QueryBatcher)",
    explicit_bucket_boundaries_advisory=[1, 2, 4, 8, 16, 32])


def default_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class TokenCounter:
    """청크 분할용 토큰 수 세기. 전체 모델 없이 토크나이저만 불러온다."""

    def __init__(self):
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL, revision=EMBEDDING_REVISION)

    def __call__(self, text: str) -> int:
        return len(self.tokenizer(text, add_special_tokens=False)["input_ids"])


class Embedder:
    model_name = EMBEDDING_MODEL
    revision = EMBEDDING_REVISION

    def __init__(self, device: str | None = None, dtype: str = EMBEDDING_DTYPE):
        import torch
        from sentence_transformers import SentenceTransformer

        # 고정한 커밋에는 pytorch_model.bin만 있다. transformers는 .bin을 불러오면 다음번을 위해 변환 PR의
        # model.safetensors(2.2GB)를 백그라운드로 받고, 다 받을 때까지 프로세스를 붙잡는다. 불러오는 가중치는
        # 고정 커밋의 .bin이라 받을 이유가 없고, 폐쇄망에서는 외부 접속 시도가 된다
        os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
        self.device = device or default_device()
        self.dtype = dtype  # 인덱스에 청크마다 기록한다. 문서는 늘 EMBEDDING_DTYPE이고, 다른 형식은 판정 실험에만 쓴다
        self.model = SentenceTransformer(EMBEDDING_MODEL, revision=EMBEDDING_REVISION, device=self.device,
                                         model_kwargs={"dtype": getattr(torch, dtype)})
        self.model.max_seq_length = MAX_SEQ_LENGTH
        for kind in ("query", "document"):  # 시계열을 0에서 시작시킨다 (auth/groups.py의 GroupCache와 같은 이유)
            _texts.add(0, {"kind": kind})

    def warm_up(self) -> None:
        """첫 인코딩에만 드는 준비 시간을 요청을 받기 전에 치른다. 운영에서 재시작 뒤 첫 쿼리가 5.5초, 이후는
        0.2~0.3초였다. 지표에는 세지 않는다."""
        self.model.encode(["준비"], normalize_embeddings=True, show_progress_bar=False)

    def encode_documents(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        # 인덱싱에서 가장 오래 걸리는 부분이라 추적에 따로 보인다. 쿼리 임베딩은 search_wiki의 단계 스팬이 잰다
        with _tracer.start_as_current_span("embed documents", attributes={"wiki.embedding.texts": len(texts)}):
            vectors = self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True)
        _texts.add(len(texts), {"kind": "document"})
        return vectors

    def encode_query(self, text: str) -> list[float]:
        return self.encode_queries([text])[0].tolist()

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        """쿼리 여러 개를 한 번에 인코딩한다. 진행 표시줄(tqdm)은 끈다. 여러 스레드가 동시에 만들고 닫다가
        `'tqdm' object has no attribute 'sp'`로 요청이 실패했다(experiments/m5-load "오류")."""
        _texts.add(len(texts), {"kind": "query"})
        return self.model.encode(texts, batch_size=len(texts), normalize_embeddings=True, show_progress_bar=False)


class QueryBatcher:
    """검색 서버의 쿼리 임베딩을 전용 스레드 하나가 묶어서 한다 (8장 "동시성과 장애 격리", experiments/m5-speedup).

    묶음 하나를 인코딩하는 동안 들어온 쿼리를 모아 다음 묶음으로 한 번에 인코딩한다. 기다리는 창을 따로 두지 않아
    요청이 하나면 곧바로 인코딩한다. 인코딩하는 스레드가 하나라, 요청 스레드 여럿이 코어 하나를 나눠 쓰며 모두
    느려지던 일(부하 측정에서 동시 10의 쿼리 임베딩이 혼자일 때의 약 11배)이 없어진다.
    """

    def __init__(self, embedder: Embedder, max_batch: int = 32):
        self.embedder = embedder
        self.max_batch = max_batch
        self._queue: queue.SimpleQueue[tuple[str, Future]] = queue.SimpleQueue()
        threading.Thread(target=self._run, name="query-embedder", daemon=True).start()

    def encode_query(self, text: str) -> list[float]:
        done: Future = Future()
        self._queue.put((text, done))
        return done.result()

    def _run(self) -> None:
        while True:
            batch = [self._queue.get()]
            while len(batch) < self.max_batch:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            _batch_sizes.record(len(batch))
            try:
                vectors = self.embedder.encode_queries([text for text, _ in batch])
            except Exception as e:  # 묶음이 실패하면 기다리던 요청 모두에 같은 오류를 돌려준다
                for _, done in batch:
                    done.set_exception(e)
                continue
            for (_, done), vector in zip(batch, vectors, strict=True):
                done.set_result(vector.tolist())
