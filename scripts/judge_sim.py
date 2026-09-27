"""ADR-20의 수치를 만드는 시뮬레이션. 골든셋 크기별로 판정 규칙이 얼마나 흔들리는지 잰다.

두 구성을 같은 문항으로 비교하면 문항마다 결과 차이는 -1, 0, +1 중 하나다
(Recall@5가 문항마다 0 또는 1이므로). 두 구성이 서로 다르게 맞히는 문항의 비율(p_d)과
실제 효과(delta)를 가정하고, 측정값만 볼 때와 ADR-20 규칙(채택·기각·보류)으로 볼 때의
판정 확률을 비교한다. 신뢰구간은 ADR-20과 같은 퍼센타일 부트스트랩으로 구한다.
문항별 차이는 이긴 수·진 수·비긴 수로 요약되므로, 부트스트랩은 이 세 수를 다항분포로
다시 뽑아 계산하고 같은 개수 조합은 결과를 재사용한다.

    uv run --with numpy python scripts/judge_sim.py
"""

from functools import cache

import numpy as np

THRESHOLD = 0.02  # ADR-02: Recall@5 +2%p
RUNS = 20_000     # 시나리오마다 가상 실험 횟수
BOOT = 10_000     # 실험마다 부트스트랩 반복 횟수 (ADR-20)
rng = np.random.default_rng(0)


@cache
def percentile_ci(n, wins, losses):
    """이긴 수와 진 수가 주어졌을 때 차이의 95% 퍼센타일 부트스트랩 구간."""
    p = [wins / n, losses / n, 1 - (wins + losses) / n]
    draws = np.random.default_rng(n * 100_003 + wins * 1_009 + losses).multinomial(n, p, BOOT)
    diffs = (draws[:, 0] - draws[:, 1]) / n
    return tuple(np.percentile(diffs, [2.5, 97.5]))


def simulate(n, p_d, delta):
    q = 0.5 + delta / (2 * p_d)  # 다르게 맞힌 문항 중 새 구성이 이긴 비율
    discordant = rng.binomial(n, p_d, RUNS)
    wins = rng.binomial(discordant, q)
    losses = discordant - wins
    diff = (wins - losses) / n
    ci = np.array([percentile_ci(n, int(won), int(lost)) for won, lost in zip(wins, losses, strict=True)])
    adopt = (ci[:, 0] > 0) & (diff >= THRESHOLD)
    reject = ci[:, 1] < THRESHOLD
    return (diff >= THRESHOLD).mean(), adopt.mean(), reject.mean(), (ci[:, 1] - ci[:, 0]).mean() / 2


def adoption_line(n, p_d):
    """불일치 문항 수를 고정했을 때 신뢰구간 하한이 0을 넘는 최소 차이."""
    d = round(n * p_d)
    for net in range(d % 2, d + 1, 2):
        wins, losses = (d + net) // 2, (d - net) // 2
        if percentile_ci(n, wins, losses)[0] > 0:
            return net / n
    return None


def main():
    print("정답 문항  p_d   실제 효과  측정값만 채택  규칙 채택  기각   보류   95% CI 반폭")
    for n in (135, 270):  # 150문항, 300문항에서 답 없음 10%를 뺀 수
        for p_d in (0.10, 0.15):
            for delta in (0.0, 0.02, 0.05, 0.08):
                naive, adopt, reject, half = simulate(n, p_d, delta)
                hold = 1 - adopt - reject
                print(f"{n:>8}  {p_d:<5} {delta * 100:>6.0f}%p  {naive:>12.1%}  {adopt:>8.1%}"
                      f"  {reject:>5.1%}  {hold:>5.1%}  ±{half * 100:.1f}%p")
        print()
    for n in (135, 270):
        for p_d in (0.10, 0.15):
            line = adoption_line(n, p_d)
            shown = f"{line * 100:.1f}%p" if line is not None else "없음"
            print(f"{n}문항, p_d={p_d}: 신뢰구간 하한이 0을 넘는 최소 차이 {shown}")
    for p_d in (0.10, 0.15):
        need = int((2.8 / THRESHOLD) ** 2 * p_d)
        print(f"p_d={p_d}: 2%p를 80% 확률로 구분하려면 정답 문항 약 {need:,}개")


if __name__ == "__main__":
    main()
