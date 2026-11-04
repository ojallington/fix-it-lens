"""The numbers the eval prints: Wilson intervals, the 3x3 confusion matrix, balanced accuracy, the label-shuffle
control. Pure functions, no I/O, so `make eval` is reproducible to the digit.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

CLASSES: tuple[str, ...] = ("done", "not_done", "cant_see")
Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials, as fractions in [0, 1]."""
    if n <= 0:
        raise ValueError("wilson needs n > 0")
    if not 0 <= k <= n:
        raise ValueError(f"wilson needs 0 <= k <= n, got k={k} n={n}")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def wilson_pct(k: int, n: int) -> tuple[float, float]:
    lo, hi = wilson(k, n)
    return round(100 * lo, 1), round(100 * hi, 1)


def wilson_count(k: int, n: int) -> tuple[float, float]:
    """The Wilson interval in the claim's own unit when the value is a count k of n: a count, so k lies inside it."""
    lo, hi = wilson(k, n)
    return round(n * lo, 1), round(n * hi, 1)


@dataclass(frozen=True)
class Confusion:
    """rows = truth, columns = prediction, both in CLASSES order."""

    matrix: tuple[tuple[int, ...], ...]
    classes: tuple[str, ...] = CLASSES

    @property
    def n(self) -> int:
        return sum(sum(r) for r in self.matrix)

    def cell(self, truth: str, pred: str) -> int:
        return self.matrix[self.classes.index(truth)][self.classes.index(pred)]

    def row_total(self, truth: str) -> int:
        return sum(self.matrix[self.classes.index(truth)])

    def recall(self, truth: str) -> float | None:
        t = self.row_total(truth)
        return self.cell(truth, truth) / t if t else None

    def balanced_accuracy(self) -> float:
        """Mean per-class recall over the classes present in the truth."""
        recalls = [r for c in self.classes if (r := self.recall(c)) is not None]
        if not recalls:
            raise ValueError("balanced accuracy of an empty matrix")
        return sum(recalls) / len(recalls)

    def accuracy(self) -> float:
        return sum(self.matrix[i][i] for i in range(len(self.classes))) / self.n

    def false_done(self, among: str = "not_done") -> tuple[int, int]:
        """(predicted done, total) among clips whose truth is `among`."""
        return self.cell(among, "done"), self.row_total(among)

    def as_lists(self) -> list[list[int]]:
        return [list(r) for r in self.matrix]


def confusion(truth: Sequence[str], pred: Sequence[str], classes: tuple[str, ...] = CLASSES) -> Confusion:
    if len(truth) != len(pred):
        raise ValueError(f"truth has {len(truth)} items, pred has {len(pred)}")
    idx = {c: i for i, c in enumerate(classes)}
    m = [[0] * len(classes) for _ in classes]
    for t, p in zip(truth, pred, strict=True):
        if t not in idx or p not in idx:
            raise ValueError(f"unknown class in ({t!r}, {p!r}); classes are {classes}")
        m[idx[t]][idx[p]] += 1
    return Confusion(tuple(tuple(r) for r in m), classes)


def shuffled_balanced_accuracy(truth: Sequence[str], pred: Sequence[str], *, seed: int = 0,
                               rounds: int = 200) -> tuple[float, float, float]:
    """Negative control on the scorer: balanced accuracy against shuffled truth labels, as (mean, p2.5, p97.5).
    A correct scorer gives about 1/len(classes) whatever the predictions are."""
    rng = random.Random(seed)
    labels = list(truth)
    scores = []
    for _ in range(rounds):
        rng.shuffle(labels)
        scores.append(confusion(labels, pred).balanced_accuracy())
    scores.sort()
    return (sum(scores) / len(scores), scores[int(0.025 * (rounds - 1))], scores[int(0.975 * (rounds - 1))])


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 100])."""
    if not values:
        raise ValueError("percentile of nothing")
    s = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(s)))
    return s[rank - 1]


def median(values: Sequence[float]) -> float:
    s = sorted(values)
    if not s:
        raise ValueError("median of nothing")
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
