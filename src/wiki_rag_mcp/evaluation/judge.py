"""ADR-20 판정: 문항별 차이를 복원 추출하는 퍼센타일 부트스트랩으로 95% 신뢰구간을 구하고 세 가지로 판정한다.

- 채택: 신뢰구간 하한 > 0 이고 측정한 차이 >= 기준값
- 기각: 신뢰구간 상한 < 기준값
- 보류: 그 밖. 보류면 구성 요소, 외부 의존, 지연을 늘리지 않는 쪽을 고른다
"""

from dataclasses import dataclass

import numpy as np

BOOTSTRAP = 10_000
SEED = 20260927  # 판정을 다시 돌려도 같은 구간이 나오게 고정한다


@dataclass(frozen=True)
class Comparison:
    base: float  # 기준 구성의 평균
    candidate: float  # 비교 구성의 평균
    diff: float  # candidate - base
    low: float  # 차이의 95% 신뢰구간 하한
    high: float
    n: int
    discordant: float  # 두 구성의 결과가 다른 문항 비율


def compare(base: list[float], candidate: list[float], resamples: int = BOOTSTRAP, seed: int = SEED) -> Comparison:
    """같은 문항 순서의 두 결과를 비교한다(짝 비교). 문항을 복원 추출해 평균 차이의 분포를 만든다."""
    a, b = np.asarray(base, dtype=float), np.asarray(candidate, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("두 결과의 문항 수가 같아야 한다")
    diffs = b - a
    rng = np.random.default_rng(seed)
    means = diffs[rng.integers(0, len(diffs), size=(resamples, len(diffs)))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return Comparison(base=float(a.mean()), candidate=float(b.mean()), diff=float(diffs.mean()), low=float(low),
                      high=float(high), n=len(diffs), discordant=float((diffs != 0).mean()))


def verdict(c: Comparison, threshold: float) -> str:
    if c.low > 0 and c.diff >= threshold:
        return "채택"
    if c.high < threshold:
        return "기각"
    return "보류"


def non_inferior(c: Comparison, margin: float) -> str:
    """나빠지지 않았는가 (experiments/m5-speedup, bf16 재판정). 속도를 얻는 변경은 개선을 채택하는 규칙 대신, 차이의
    95% 신뢰구간 하한이 -margin 이상이면 같은 품질로 본다. 증거가 부족하면 바꾸지 않는다."""
    return "같음" if c.low >= -margin else "나빠졌을 수 있음"
