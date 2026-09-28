"""평가 지표와 ADR-20 판정의 정의를 고정한다. 판정 기준은 결과를 보기 전에 정해 두는 것이라 테스트로 묶는다."""

import pytest

from wiki_rag_mcp.evaluation.judge import compare, non_inferior, verdict
from wiki_rag_mcp.evaluation.metrics import mrr_at, ndcg_at, recall_at


def test_recall_counts_any_relevant_doc_in_top5_results():
    assert recall_at(["a", "a", "b", "c", "d", "x"], {"x"}) == 0.0
    assert recall_at(["a", "a", "b", "c", "x"], {"x", "y"}) == 1.0


def test_mrr_uses_rank_of_first_relevant_result():
    assert mrr_at(["a", "a", "x", "x"], {"x"}) == pytest.approx(1 / 3)
    assert mrr_at(["a"] * 10 + ["x"], {"x"}) == 0.0


def test_ndcg_counts_a_document_once():
    assert ndcg_at(["x", "x", "y"], {"x", "y"}) == pytest.approx((1 + 1 / 2) / (1 + 1 / 1.5849625007211562))
    assert ndcg_at(["x"], {"x"}) == 1.0
    assert ndcg_at(["a"], set()) == 0.0


def test_compare_is_paired_and_reproducible():
    base = [0.0] * 50 + [1.0] * 50
    cand = [1.0] * 60 + [1.0] * 40
    c = compare(base, cand)
    assert c.diff == pytest.approx(0.5) and c.low > 0 and c == compare(base, cand)
    assert c.discordant == pytest.approx(0.5)


def test_verdict_rules_follow_adr20():
    same = compare([1.0, 0.0] * 135, [1.0, 0.0] * 135)
    assert verdict(same, 0.02) == "기각"  # 차이 0, 구간 [0, 0] 상한이 기준값보다 작다
    better = compare([0.0] * 270, [1.0] * 30 + [0.0] * 240)
    assert better.low > 0 and verdict(better, 0.02) == "채택"
    noisy = compare([1.0, 0.0] * 135, [1.0, 0.0] * 130 + [1.0, 1.0] * 5)
    assert verdict(noisy, 0.02) == "보류"


def test_non_inferiority_needs_the_lower_bound_within_the_margin():
    same = compare([1.0, 0.0] * 135, [1.0, 0.0] * 135)
    assert non_inferior(same, 0.02) == "같음"
    # 270문항 중 12문항을 새로 놓침: 차이 -4.4%p, 신뢰구간 하한이 -2%p보다 낮다
    worse = compare([1.0] * 270, [1.0] * 258 + [0.0] * 12)
    assert worse.low < -0.02 and non_inferior(worse, 0.02) == "나빠졌을 수 있음"
    # 한 문항만 달라져도 하한이 허용 폭 안이면 같다고 본다
    one = compare([1.0] * 270, [1.0] * 269 + [0.0])
    assert one.low >= -0.02 and non_inferior(one, 0.02) == "같음"
