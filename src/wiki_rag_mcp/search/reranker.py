"""cross-encoder 리랭커 (ADR-13). 판정에서 채택될 때만 서버가 쓴다.

질문과 후보 청크를 한 쌍씩 모델에 넣어 관련도를 다시 매긴다. 벡터 검색보다 정확하지만 후보마다 모델을
돌려야 해서 지연 예산(8장)의 절반 이상을 쓴다.
"""

from typing import Any

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
CANDIDATES = 30  # 리랭킹할 후보 수 (8장 지연 예산의 "상위 30개")
MAX_LENGTH = 512  # 질문과 청크를 합친 토큰 상한. 청크(최대 500토큰)가 드물게 끝이 잘린다


def passage(hit: dict[str, Any]) -> str:
    """리랭커에 넣는 청크 표현. 임베딩의 맥락 헤더처럼 문서 제목과 섹션 경로를 앞에 붙인다."""
    head = " > ".join([hit["title"], *(hit.get("section_path") or [])])
    return f"{head}\n{hit['text']}"


class Reranker:
    model_name = RERANKER_MODEL
    revision = RERANKER_REVISION

    def __init__(self, device: str = "cpu"):
        import torch
        from sentence_transformers import CrossEncoder

        self.device = device
        self.model = CrossEncoder(RERANKER_MODEL, revision=RERANKER_REVISION, device=device, max_length=MAX_LENGTH,
                                  model_kwargs={"dtype": torch.float32})

    def rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hits:
            return []
        scores = self.model.predict([(query, passage(h)) for h in hits], batch_size=len(hits))
        ranked = sorted(zip(hits, scores, strict=True), key=lambda hs: -float(hs[1]))
        return [{**h, "score": float(s)} for h, s in ranked]
