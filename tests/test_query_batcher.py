"""쿼리 묶음 처리(QueryBatcher, experiments/m5-speedup). 인코딩 중에 몰린 쿼리가 다음 묶음이 되고, 요청마다 자기
벡터를 돌려받으며, 묶음이 실패해도 인코딩 스레드는 살아 있는지 본다."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from wiki_rag_mcp.indexing.embedder import QueryBatcher


class GatedEncoder:
    """첫 묶음을 gate가 열릴 때까지 붙잡는다. 그동안 들어온 쿼리가 다음 묶음으로 모인다."""

    def __init__(self, fail_on: str | None = None):
        self.gate = threading.Event()
        self.batches: list[list[str]] = []
        self.fail_on = fail_on

    def encode_queries(self, texts):
        self.batches.append(list(texts))
        if len(self.batches) == 1:
            self.gate.wait(5)
        if self.fail_on in texts:
            raise RuntimeError("인코딩 실패 (테스트)")
        return np.array([[float(len(t)), float(ord(t[-1]))] for t in texts])


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "시간 안에 조건이 맞지 않았다"
        time.sleep(0.01)


def test_queries_arriving_during_a_batch_are_encoded_together():
    encoder = GatedEncoder()
    batcher = QueryBatcher(encoder)
    texts = ["첫", "둘째 쿼리", "셋", "넷째", "다섯째 쿼리"]
    with ThreadPoolExecutor(len(texts)) as pool:
        first = pool.submit(batcher.encode_query, texts[0])
        wait_until(lambda: len(encoder.batches) == 1)
        rest = [pool.submit(batcher.encode_query, t) for t in texts[1:]]
        wait_until(lambda: batcher._queue.qsize() == len(rest))
        encoder.gate.set()
        results = [first.result(5), *(f.result(5) for f in rest)]
    assert [len(b) for b in encoder.batches] == [1, 4]
    assert results == [[float(len(t)), float(ord(t[-1]))] for t in texts]  # 요청마다 자기 벡터


def test_a_failed_batch_fails_its_requests_and_the_next_batch_still_runs():
    encoder = GatedEncoder(fail_on="실패")
    encoder.gate.set()
    batcher = QueryBatcher(encoder)
    with pytest.raises(RuntimeError, match="인코딩 실패"):
        batcher.encode_query("실패")
    assert batcher.encode_query("정상") == [2.0, float(ord("상"))]
