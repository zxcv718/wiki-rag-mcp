"""CI 회귀 검사의 임베딩 캐시와 통과 기준. 모델을 받지 않도록 가짜 인코더로 캐시 동작만 본다."""

import numpy as np
import pytest

from wiki_rag_mcp.evaluation import regression
from wiki_rag_mcp.evaluation.regression import CachedEncoder, embedding_namespace, passed

DIM = 4


class CountingEncoder:
    """문장마다 정해진 벡터를 돌려주고, 불린 횟수와 받은 문장을 기록한다."""

    def __init__(self):
        self.doc_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    @staticmethod
    def _vec(text: str, offset: float) -> np.ndarray:
        return np.array([len(text), offset, 1.0, 2.0], dtype=np.float32)

    def encode_documents(self, texts):
        self.doc_calls.append(list(texts))
        return np.array([self._vec(t, 0.5) for t in texts])

    def encode_query(self, text):
        self.query_calls.append(text)
        return self._vec(text, 0.25).tolist()


class Factory:
    def __init__(self):
        self.created: list[CountingEncoder] = []

    def __call__(self):
        self.created.append(CountingEncoder())
        return self.created[-1]


def make(path, namespace="ns-a"):
    factory = Factory()
    return CachedEncoder(path, factory=factory, namespace=namespace), factory


def test_misses_are_embedded_in_one_call_and_the_next_run_never_loads_the_model(tmp_path):
    path = tmp_path / "cache.npz"
    cold, factory = make(path)
    first = cold.encode_documents(["가", "나다", "가"])
    assert len(factory.created) == 1 and factory.created[0].doc_calls == [["가", "나다"]]
    assert (cold.hits, cold.misses) == (0, 3)
    cold.encode_query("질문")
    cold.save()

    warm, factory = make(path)
    assert warm.loaded == 3
    np.testing.assert_array_equal(warm.encode_documents(["가", "나다", "가"]), first)
    assert warm.encode_query("질문") == CountingEncoder._vec("질문", 0.25).tolist()
    assert (warm.hits, warm.misses) == (4, 0)
    assert factory.created == [] and not warm.model_loaded


def test_only_new_texts_reach_the_model(tmp_path):
    path = tmp_path / "cache.npz"
    first, _ = make(path)
    first.encode_documents(["가", "나"])
    first.save()
    second, factory = make(path)
    out = second.encode_documents(["가", "다", "나"])
    assert factory.created[0].doc_calls == [["다"]]
    assert (second.hits, second.misses) == (2, 1)
    assert out.shape == (3, DIM) and out.dtype == np.float32


def test_documents_and_queries_with_the_same_text_do_not_collide(tmp_path):
    enc, factory = make(tmp_path / "cache.npz")
    doc = enc.encode_documents(["같은 문장"])[0]
    query = enc.encode_query("같은 문장")
    assert enc.misses == 2 and factory.created[0].query_calls == ["같은 문장"]
    assert doc[1] == 0.5 and query[1] == 0.25


def test_changed_namespace_discards_the_whole_cache(tmp_path):
    path = tmp_path / "cache.npz"
    old, _ = make(path, namespace="ns-a")
    old.encode_documents(["가", "나"])
    old.save()
    new, factory = make(path, namespace="ns-b")
    assert new.loaded == 0
    new.encode_documents(["가"])
    assert factory.created[0].doc_calls == [["가"]]
    new.save()
    # 새 namespace로 저장하면 옛 항목은 남지 않는다
    again, _ = make(path, namespace="ns-b")
    assert again.loaded == 1


def test_save_roundtrip_keeps_exact_float32_values_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "sub" / "cache.npz"
    enc, _ = make(path)
    vectors = enc.encode_documents(["가", "나다라"])
    query = enc.encode_query("질문")
    enc.save()
    assert [p.name for p in path.parent.iterdir()] == ["cache.npz"]
    loaded, _ = make(path)
    np.testing.assert_array_equal(loaded.encode_documents(["가", "나다라"]), vectors)
    assert loaded.encode_query("질문") == query


def test_namespace_follows_the_pinned_model(monkeypatch):
    before = embedding_namespace()
    assert before == embedding_namespace() and len(before) == 64
    monkeypatch.setattr(regression, "EMBEDDING_REVISION", "another-commit")
    assert embedding_namespace() != before


@pytest.mark.parametrize(("recall", "baseline", "violations", "expected"), [
    (0.9704, 0.9704, 0, True),
    (0.9505, 0.9704, 0, True),  # 1.99%p 하락
    (0.95, 0.97, 0, False),  # 딱 2%p 하락. 부동소수 뺄셈은 0.020000000000000018
    (0.8806, 0.9006, 0, False),  # 딱 2%p 하락. 부동소수 뺄셈은 0.019999999999999907
    (0.9503, 0.9704, 0, False),
    (0.99, 0.9704, 1, False),  # 지표가 좋아도 권한 위반 1건이면 실패
])
def test_gate_blocks_a_drop_of_two_points_or_more_and_any_violation(recall, baseline, violations, expected):
    assert passed(recall, baseline, violations) is expected
