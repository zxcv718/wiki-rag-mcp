"""여러 검색 결과의 순위를 합친다 (ADR-12)."""

from collections.abc import Sequence

RRF_K = 60  # Cormack 외(SIGIR 2009)의 기본값. 골든셋 결과를 보고 바꾸지 않는다 (ADR-20)


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion. 순위만 쓰므로 BM25와 코사인 점수의 척도를 맞출 필요가 없다.

    점수가 같으면 더 높은 순위로 처음 나온 id를, 순위도 같으면 앞 목록의 id를 앞에 둔다(결과가 매번 같도록).
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[int, int]] = {}  # (가장 높은 순위, 그 목록 번호)
    for list_no, ranking in enumerate(rankings):
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen[item] = min(first_seen.get(item, (rank, list_no)), (rank, list_no))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
