"""Small statistics helpers. No dependencies, so the harness stays runnable anywhere."""

from __future__ import annotations

from typing import Iterable, Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; `q` in [0, 100]."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(q / 100.0 * len(ordered))))
    return ordered[rank - 1]


def median(values: Sequence[float]) -> float:
    return percentile(values, 50)


def jaccard(a: Iterable, b: Iterable) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def kendall_tau(a: Sequence, b: Sequence) -> float:
    """Rank correlation over the items the two rankings share.

    Returns NaN when fewer than two items are common, where the statistic is
    undefined rather than zero.
    """
    common = [item for item in a if item in b]
    if len(common) < 2:
        return float("nan")
    rank_b = {item: index for index, item in enumerate(b)}
    concordant = discordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            left = rank_b[common[i]] - rank_b[common[j]]
            concordant += left < 0
            discordant += left > 0
    total = concordant + discordant
    return (concordant - discordant) / total if total else float("nan")
