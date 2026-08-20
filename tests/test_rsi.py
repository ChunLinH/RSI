import pytest

from rsi_monitor.indicators.rsi import RsiCalculator


def test_returns_none_until_enough_prices() -> None:
    assert RsiCalculator(3).calculate([10, 11, 12]) is None


def test_returns_extreme_values_for_one_direction_moves() -> None:
    calculator = RsiCalculator(3)

    assert calculator.calculate([1, 2, 3, 4]) == 100.0
    assert calculator.calculate([4, 3, 2, 1]) == 0.0


def test_uses_wilder_smoothing() -> None:
    rsi = RsiCalculator(2).calculate([1, 2, 1, 2, 1])

    assert rsi == pytest.approx(37.5)
