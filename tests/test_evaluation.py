"""평가 지표와 ADR-20 판정의 정의를 고정한다. 판정 기준은 결과를 보기 전에 정해 두는 것이라 테스트로 묶는다."""

import pytest

from wiki_rag_mcp.evaluation.judge import compare, verdict
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

