class RsiCalculator:
    """使用 Wilder 平滑法计算最后一个 RSI 值。"""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period 必须大于 0")
        self._period = period

    @property
    def period(self) -> int:
        return self._period

    def calculate(self, prices: list[float]) -> float | None:
        if len(prices) < self._period + 1:
            return None

        changes = [current - previous for previous, current in zip(prices, prices[1:])]
        gains = [max(change, 0.0) for change in changes]
        losses = [max(-change, 0.0) for change in changes]
        average_gain = sum(gains[: self._period]) / self._period
        average_loss = sum(losses[: self._period]) / self._period

        for gain, loss in zip(gains[self._period :], losses[self._period :]):
            average_gain = (average_gain * (self._period - 1) + gain) / self._period
            average_loss = (average_loss * (self._period - 1) + loss) / self._period

        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0
        if average_gain == 0:
            return 0.0
        relative_strength = average_gain / average_loss
        return 100 - (100 / (1 + relative_strength))
