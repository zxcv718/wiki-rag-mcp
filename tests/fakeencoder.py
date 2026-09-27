"""임베딩 모델 대신 쓰는 가짜 인코더. 권한·이벤트 시험은 검색 품질이 아니라 누출과 반영을 보므로 모델이 필요 없다.

같은 문장에는 늘 같은 벡터를 주고, 문장마다 방향이 다르다.
"""

import hashlib

import numpy as np

DIM = 16


class FakeEncoder:
    model_name, revision, dtype = "fake", "0", "float32"

    @staticmethod
    def _vec(text: str) -> np.ndarray:
        raw = np.frombuffer(hashlib.sha256(text.encode()).digest()[:DIM], dtype=np.uint8).astype(np.float32) + 1
        return raw / np.linalg.norm(raw)

    def encode_documents(self, texts):
        return np.array([self._vec(t) for t in texts])

    def encode_query(self, text):
        return self._vec(text)


def count_words(text: str) -> int:
    return len(text.split())
