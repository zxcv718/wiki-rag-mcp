"""MCP 서버 안에서 직접 돌리는 임베딩 모델 (ADR-11).

모델 이름, 커밋, 형식(float32)을 고정해서 불러온다. 최신 transformers는 모델 파일에 적힌 형식 그대로
불러오므로 형식을 지정하지 않으면 장비에 따라 속도가 크게 달라진다(experiments/embedding-model).
bge-m3의 문장 벡터는 CLS 토큰 벡터이고, sentence-transformers 설정이 이를 따른다.
"""

import os

import numpy as np

from wiki_rag_mcp.config import EMBEDDING_DTYPE, EMBEDDING_MODEL, EMBEDDING_REVISION

MAX_SEQ_LENGTH = 2048  # 청크는 500토큰 안팎이지만 자르지 않는 큰 표가 있어 여유를 둔다


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
    dtype = EMBEDDING_DTYPE

    def __init__(self, device: str | None = None):
        import torch
        from sentence_transformers import SentenceTransformer

        # 고정한 커밋에는 pytorch_model.bin만 있다. transformers는 .bin을 불러오면 다음번을 위해 변환 PR의
        # model.safetensors(2.2GB)를 백그라운드로 받고, 다 받을 때까지 프로세스를 붙잡는다. 불러오는 가중치는
        # 고정 커밋의 .bin이라 받을 이유가 없고, 폐쇄망에서는 외부 접속 시도가 된다
        os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
        self.device = device or default_device()
        self.model = SentenceTransformer(EMBEDDING_MODEL, revision=EMBEDDING_REVISION, device=self.device,
                                         model_kwargs={"dtype": getattr(torch, EMBEDDING_DTYPE)})
        self.model.max_seq_length = MAX_SEQ_LENGTH

    def encode_documents(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        return self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True)

    def encode_query(self, text: str) -> list[float]:
        return self.model.encode([text], normalize_embeddings=True)[0].tolist()
