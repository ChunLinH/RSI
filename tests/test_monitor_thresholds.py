from datetime import datetime

from rsi_monitor.indicators.rsi import RsiCalculator
from rsi_monitor.market_data.base import MarketDataProvider, MarketQuote
from rsi_monitor.monitoring.engine import MonitorEngine
from rsi_monitor.notifications.console import ConsoleNotificationService
from rsi_monitor.signals.evaluator import SignalDetector


class PriceProvider(MarketDataProvider):
    def __init__(self, prices: list[float]) -> None:
        self._prices = iter(prices)

    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote("159952", next(self._prices), datetime.now().astimezone())


def test_updating_thresholds_does_not_emit_an_immediate_duplicate_notification() -> None:
    engine = MonitorEngine(
        provider=PriceProvider([1, 2, 3, 4, 5]),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=ConsoleNotificationService(),
        price_history_size=10,
    )

    for _ in range(4):
        engine.tick()
    engine.update_signal_thresholds(25, 75)
    snapshot = engine.tick()

    assert snapshot.signal.value == "卖出"
    assert snapshot.notification_status == "等待信号"
