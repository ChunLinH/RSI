import pytest

from rsi_monitor.indicators.series import (
    calculate_kdj_series,
    calculate_macd_series,
    calculate_rsi_series,
    calculate_sma_series,
)


def test_rsi_series_matches_expected_extremes() -> None:
    rising = calculate_rsi_series([1, 2, 3, 4, 5, 6, 7], 6)
    flat = calculate_rsi_series([1, 1, 1, 1, 1, 1, 1], 6)

    assert rising[-1] == 100
    assert flat[-1] == 50


def test_macd_and_kdj_series_keep_input_length() -> None:
    closes = [10, 11, 10.5, 12, 11.5, 13, 12.5, 14, 13.5]

    dif, dea, histogram = calculate_macd_series(closes)
    k, d, j = calculate_kdj_series(closes, closes, closes)

    assert len(dif) == len(dea) == len(histogram) == len(closes)
    assert len(k) == len(d) == len(j) == len(closes)
    assert histogram[0] == pytest.approx(0)


def test_sma_series_uses_requested_rolling_window() -> None:
    values = calculate_sma_series([1, 2, 3, 4, 5], 3)

    assert values == (None, None, 2.0, 3.0, 4.0)
