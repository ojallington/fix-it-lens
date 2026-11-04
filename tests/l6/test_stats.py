import pytest

from eval.stats import CLASSES, confusion, median, percentile, shuffled_balanced_accuracy, wilson, wilson_pct


@pytest.mark.parametrize(("k", "n", "lo", "hi"), [
    (0, 10, 0.0, 0.2775328),       # standard Wilson reference values
    (5, 10, 0.2365931, 0.7634069),
    (2, 40, 0.0138, 0.1650),       # the design's "2 of 40 (Wilson 95% CI 1.4-16.5%)"
    (40, 40, 0.9124, 1.0),
])
def test_wilson_reference(k: int, n: int, lo: float, hi: float) -> None:
    got = wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=1e-4)
    assert got[1] == pytest.approx(hi, abs=1e-4)


def test_wilson_pct_matches_the_design() -> None:
    assert wilson_pct(2, 40) == (1.4, 16.5)
    assert wilson_pct(0, 80)[1] == 4.6
    assert wilson_pct(1, 80)[1] == 6.7


def test_wilson_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        wilson(1, 0)
    with pytest.raises(ValueError):
        wilson(5, 4)


def test_confusion_sums_to_n_and_balanced_accuracy() -> None:
    truth = ["done"] * 4 + ["not_done"] * 4 + ["cant_see"] * 2
    pred = ["done", "done", "done", "cant_see", "done", "not_done", "not_done", "not_done", "cant_see", "done"]
    m = confusion(truth, pred)
    assert m.n == len(truth) == sum(map(sum, m.as_lists()))
    assert m.cell("not_done", "done") == 1
    assert m.false_done() == (1, 4)
    assert m.balanced_accuracy() == pytest.approx((3 / 4 + 3 / 4 + 1 / 2) / 3)


def test_confusion_rejects_unknown_class() -> None:
    with pytest.raises(ValueError):
        confusion(["done"], ["maybe"])


def test_shuffled_labels_collapse_to_chance() -> None:
    truth = [c for c in CLASSES for _ in range(40)]
    perfect = list(truth)
    assert confusion(truth, perfect).balanced_accuracy() == 1.0
    mean, lo, hi = shuffled_balanced_accuracy(truth, perfect, seed=1)
    assert lo <= 1 / 3 <= hi
    assert abs(mean - 1 / 3) < 0.03


def test_percentiles() -> None:
    assert median([3, 1, 2, 10]) == 2.5
    assert percentile(list(range(1, 101)), 95) == 95
