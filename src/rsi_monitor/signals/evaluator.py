from dataclasses import dataclass
from enum import Enum


class SignalCondition(str, Enum):
    NONE = "无信号"
    BUY = "买入"
    SELL = "卖出"


@dataclass(frozen=True)
class SignalEvent:
    condition: SignalCondition
    should_notify: bool


class SignalDetector:
    """识别 RSI 阈值状态，并仅在进入买入或卖出状态时通知一次。"""

    def __init__(self, buy_threshold: float, sell_threshold: float) -> None:
        if not 0 <= buy_threshold < sell_threshold <= 100:
            raise ValueError("RSI 阈值无效")
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
        self._active_condition = SignalCondition.NONE

    def evaluate(self, rsi: float | None) -> SignalEvent:
        condition = self._classify(rsi)
        should_notify = condition is not SignalCondition.NONE and condition is not self._active_condition
        self._active_condition = condition
        return SignalEvent(condition=condition, should_notify=should_notify)

    def _classify(self, rsi: float | None) -> SignalCondition:
        if rsi is None:
            return SignalCondition.NONE
        if rsi <= self._buy_threshold:
            return SignalCondition.BUY
        if rsi >= self._sell_threshold:
            return SignalCondition.SELL
        return SignalCondition.NONE
