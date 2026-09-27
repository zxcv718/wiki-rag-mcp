"""상용 임베딩 비교군 (ADR-11). 판정 실험에만 쓰고 서버에는 넣지 않는다.

`gemini-embedding-001`의 REST API를 부른다. 문서는 RETRIEVAL_DOCUMENT, 질문은 RETRIEVAL_QUERY 작업 유형으로
임베딩한다. 기본 차원(3072)은 정규화되어 나오지만, 코사인 비교가 정확하도록 한 번 더 정규화한다.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
BATCH = 50
MAX_TRIES = 8


def api_key(env_file: Path = Path(".env")) -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit(".env에 GEMINI_API_KEY가 없습니다. Google AI Studio에서 발급해 넣으세요 (.env.example 참고).")
    return key


class GeminiEmbedder:
    model_name = "gemini-embedding-001"
    revision = "api-v1beta"  # API 모델은 버전을 고정할 수 없어, 측정 날짜를 결과와 함께 적는다
    dtype = "float32"
    dim = 3072

    def __init__(self, key: str | None = None):
        self.key = key or api_key()
        self._queries: dict[str, np.ndarray] = {}

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        for attempt in range(MAX_TRIES):
            try:
                with urllib.request.urlopen(request, timeout=120) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                # 무료 등급의 호출 한도(429)나 일시 오류는 기다렸다 다시 부른다
                if e.code not in (429, 500, 503) or attempt == MAX_TRIES - 1:
                    raise RuntimeError(f"Gemini API 오류 {e.code}: {e.read()[:300]!r}") from e
                time.sleep(min(90, 5 * 2**attempt))
        raise AssertionError("unreachable")

    def _embed(self, texts: list[str], task: str) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), BATCH):
            body = {"requests": [{"model": "models/gemini-embedding-001", "content": {"parts": [{"text": t}]},
                                  "taskType": task} for t in texts[start : start + BATCH]]}
            vectors += [e["values"] for e in self._post(body)["embeddings"]]
        array = np.array(vectors, dtype=np.float32)
        return array / np.linalg.norm(array, axis=1, keepdims=True)

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def encode_queries(self, texts: list[str]) -> None:
        """평가 전에 질문을 한꺼번에 임베딩해 둔다. 질문마다 부르면 무료 등급 한도에 걸린다."""
        missing = [t for t in dict.fromkeys(texts) if t not in self._queries]
        if missing:
            self._queries.update(zip(missing, self._embed(missing, "RETRIEVAL_QUERY"), strict=True))

    def encode_query(self, text: str) -> list[float]:
        if text not in self._queries:
            self.encode_queries([text])
        return self._queries[text].tolist()
