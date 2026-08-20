from collections.abc import Sequence


def calculate_sma_series(
    values: Sequence[float], period: int
) -> tuple[float | None, ...]:
    if period < 1:
        raise ValueError("period 必须大于 0")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    rolling_sum = sum(values[:period])
    result[period - 1] = rolling_sum / period
    for index in range(period, len(values)):
        rolling_sum += values[index] - values[index - period]
        result[index] = rolling_sum / period
    return tuple(result)


def calculate_rsi_series(
    closes: Sequence[float], period: int = 6
) -> tuple[float | None, ...]:
    if period < 1:
        raise ValueError("period 必须大于 0")
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return tuple(result)
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index + 1] = _rsi_value(average_gain, average_loss)
    return tuple(result)


def calculate_macd_series(
    closes: Sequence[float],
    short_period: int = 12,
    long_period: int = 26,
    signal_period: int = 9,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if not closes:
        return (), (), ()
    short_ema = _ema(closes, short_period)
    long_ema = _ema(closes, long_period)
    dif = tuple(short - long for short, long in zip(short_ema, long_ema))
    dea = _ema(dif, signal_period)
    histogram = tuple((value - signal) * 2 for value, signal in zip(dif, dea))
    return dif, dea, histogram


def calculate_kdj_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 9,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("KDJ 输入长度必须一致")
    k_values: list[float] = []
    d_values: list[float] = []
    j_values: list[float] = []
    k = 50.0
    d = 50.0
    for index, close in enumerate(closes):
        start = max(index - period + 1, 0)
        highest = max(highs[start : index + 1])
        lowest = min(lows[start : index + 1])
        rsv = 50.0 if highest == lowest else (close - lowest) / (highest - lowest) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        j = 3 * k - 2 * d
        k_values.append(k)
        d_values.append(d)
        j_values.append(j)
    return tuple(k_values), tuple(d_values), tuple(j_values)


def _ema(values: Sequence[float], period: int) -> tuple[float, ...]:
    if period < 1:
        raise ValueError("EMA 周期必须大于 0")
    if not values:
        return ()
    multiplier = 2 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(float(value) * multiplier + result[-1] * (1 - multiplier))
    return tuple(result)


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    if average_gain == 0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)
