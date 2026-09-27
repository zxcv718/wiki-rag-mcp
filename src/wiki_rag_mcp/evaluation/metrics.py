"""검색 지표 (7장 "지표"). 정의를 직접 써서 통제한다 (ADR-14 도구 선택).

결과 목록은 search_wiki가 돌려주는 청크 순서 그대로다. 청크마다 그 청크의 문서가 정답인지로 관련도를 정한다.
한 문서의 여러 청크가 상위를 차지하면 그만큼 다른 문서가 밀려나는데, 이것도 사용자가 보는 결과라 그대로 잰다.

- Recall@5: 상위 5개 결과의 문서 가운데 정답 문서가 하나라도 있으면 1 (ADR-20)
- MRR@10: 정답 문서가 처음 나온 결과 순위의 역수. 10위 안에 없으면 0
- nDCG@10: 관련도는 0 또는 1이고, 같은 문서는 처음 나올 때만 이득으로 센다. 이상적인 순위는 정답 문서
  min(정답 수, 10)개가 맨 앞에 오는 경우다
"""

import math
from collections.abc import Sequence


def recall_at(docs: Sequence[str], relevant: set[str], k: int = 5) -> float:
    return 1.0 if any(d in relevant for d in docs[:k]) else 0.0


def mrr_at(docs: Sequence[str], relevant: set[str], k: int = 10) -> float:
    for rank, d in enumerate(docs[:k], 1):
        if d in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at(docs: Sequence[str], relevant: set[str], k: int = 10) -> float:
    seen: set[str] = set()
    dcg = 0.0
    for rank, d in enumerate(docs[:k], 1):
        if d in relevant and d not in seen:
            dcg += 1.0 / math.log2(rank + 1)
        seen.add(d)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


METRICS = {"recall@5": recall_at, "mrr@10": mrr_at, "ndcg@10": ndcg_at}
