from datetime import datetime

from rsi_monitor.indicators.rsi import RsiCalculator
import pytest

from rsi_monitor.market_data.base import (
    ChartMarketDataProvider,
    ChartSeries,
    HistoricalMarketDataProvider,
    MarketBar,
    MarketDataError,
    MarketDataProvider,
    MarketQuote,
)
from rsi_monitor.monitoring.engine import MonitorEngine
from rsi_monitor.notifications.base import (
    NotificationMessage,
    NotificationResult,
    NotificationService,
)
from rsi_monitor.signals.evaluator import SignalCondition, SignalDetector


class FixedQuoteProvider(MarketDataProvider):
    def __init__(self, prices: list[float]) -> None:
        self._prices = iter(prices)

    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote("TEST", next(self._prices), datetime.now().astimezone())


class CollectingNotifier(NotificationService):
    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> NotificationResult:
        self.messages.append(message)
        return NotificationResult(sent=True, status="已发送")


def test_engine_runs_full_flow_and_deduplicates_notifications() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=FixedQuoteProvider([1, 2, 3, 4, 5, 6, 7]),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
        monitoring_period="1min",
    )

    snapshots = [engine.tick() for _ in range(7)]

    assert snapshots[2].rsi is None
    assert snapshots[3].signal is SignalCondition.SELL
    assert snapshots[3].notification_status == "已发送"
    assert snapshots[4].signal is SignalCondition.SELL
    assert snapshots[4].notification_status == "等待信号"
    assert len(notifier.messages) == 1
    assert "🔴 卖出｜TEST" == notifier.messages[0].title
    assert "标的：TEST" in notifier.messages[0].content
    assert "周期：1min" in notifier.messages[0].content


def test_engine_calculates_three_rsi_periods_but_signals_on_rsi_six() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=FixedQuoteProvider(list(range(1, 27))),
        rsi_calculator=RsiCalculator(6),
        additional_rsi_calculators={12: RsiCalculator(12), 24: RsiCalculator(24)},
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=30,
        monitoring_period="1min",
    )

    snapshots = [engine.tick() for _ in range(26)]

    assert snapshots[24].rsi_values == {6: 100.0, 12: 100.0, 24: 100.0}
    assert snapshots[6].signal is SignalCondition.SELL
    assert len(notifier.messages) == 1
    assert "RSI(6)：100.00" in notifier.messages[0].content


class HistoricalQuoteProvider(HistoricalMarketDataProvider):
    def __init__(self, prices: list[float]) -> None:
        self._quotes = [
            MarketQuote("TEST", price, datetime.now().astimezone(), name="Test ETF")
            for price in prices
        ]

    def fetch_latest_quote(self) -> MarketQuote:
        return self._quotes[-1]

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        return self._quotes[-limit:]


def test_engine_uses_real_provider_history_for_first_rsi() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=HistoricalQuoteProvider([1, 2, 3, 4]),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
    )

    snapshot = engine.tick()

    assert snapshot.rsi == 100.0
    assert snapshot.signal is SignalCondition.SELL
    assert snapshot.connection_status == "已连接"
    assert snapshot.price_history == (1, 2, 3, 4)
    assert len(notifier.messages) == 1


class LiveHistoricalQuoteProvider(HistoricalMarketDataProvider):
    def __init__(self) -> None:
        timestamp = datetime(2026, 8, 12, 9, 30)
        self._history = [
            MarketQuote("TEST", price, timestamp, name="Test ETF")
            for price in (1, 2, 3, 4)
        ]
        self._live_quotes = iter(
            [
                MarketQuote("TEST", 4.1, timestamp.replace(second=5), name="Test ETF"),
                MarketQuote("TEST", 4.2, timestamp.replace(second=10), name="Test ETF"),
            ]
        )
        self.history_requests = 0

    def fetch_latest_quote(self) -> MarketQuote:
        return next(self._live_quotes)

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        self.history_requests += 1
        return self._history[-limit:]


def test_live_updates_refresh_rsi_without_reloading_history_or_repeating_notification() -> None:
    notifier = CollectingNotifier()
    provider = LiveHistoricalQuoteProvider()
    engine = MonitorEngine(
        provider=provider,
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
        monitoring_period="1min",
    )

    first_snapshot = engine.tick()
    second_snapshot = engine.tick()

    assert provider.history_requests == 1
    assert first_snapshot.quote.price == 4.1
    assert second_snapshot.quote.price == 4.2
    assert first_snapshot.rsi == 100.0
    assert second_snapshot.rsi == 100.0
    assert first_snapshot.notification_status == "已发送"
    assert second_snapshot.notification_status == "等待信号"
    assert len(notifier.messages) == 1


class FailingQuoteProvider(MarketDataProvider):
    def __init__(self) -> None:
        self._values: list[float | Exception] = [1, 2, 3, 4, MarketDataError("网络超时"), 5]

    def fetch_latest_quote(self) -> MarketQuote:
        value = self._values.pop(0)
        if isinstance(value, Exception):
            raise value
        return MarketQuote("TEST", value, datetime.now().astimezone())


def test_failed_quote_does_not_create_or_reset_a_signal() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=FailingQuoteProvider(),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
    )

    for _ in range(4):
        engine.tick()
    with pytest.raises(MarketDataError, match="网络超时"):
        engine.tick()
    snapshot = engine.tick()

    assert snapshot.signal is SignalCondition.SELL
    assert snapshot.notification_status == "等待信号"
    assert len(notifier.messages) == 1


class CachedQuoteProvider(MarketDataProvider):
    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote(
            "TEST",
            1.0,
            datetime.now().astimezone(),
            is_stale=True,
            is_trading=False,
        )


def test_cached_quote_is_displayed_but_never_creates_a_signal() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=CachedQuoteProvider(),
        rsi_calculator=RsiCalculator(2),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
    )

    snapshots = [engine.tick() for _ in range(3)]

    assert snapshots[-1].rsi == 50.0
    assert snapshots[-1].signal is SignalCondition.NONE
    assert snapshots[-1].connection_status == "本地缓存（行情连接失败）"
    assert snapshots[-1].notification_status == "未发送（本地缓存）"
    assert notifier.messages == []


class FailingHistoryProvider(HistoricalMarketDataProvider):
    def __init__(self) -> None:
        self.history_requests = 0

    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote("TEST", 2.2, datetime.now().astimezone(), name="Test ETF")

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        self.history_requests += 1
        raise MarketDataError("历史接口不可用")


def test_history_failure_keeps_latest_price_visible_and_suppresses_signal() -> None:
    provider = FailingHistoryProvider()
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=provider,
        rsi_calculator=RsiCalculator(2),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
        history_retry_delay_seconds=30,
    )

    first = engine.tick()
    second = engine.tick()

    assert first.quote.price == 2.2
    assert first.connection_status == "已连接（历史 K 线重试中）"
    assert first.notification_status == "未发送（历史数据加载中）"
    assert second.quote.price == 2.2
    assert provider.history_requests == 1
    assert notifier.messages == []


class CachedHistoryProvider(HistoricalMarketDataProvider):
    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote("TEST", 4.0, datetime.now().astimezone(), name="Test ETF")

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        return [
            MarketQuote(
                "TEST",
                price,
                datetime.now().astimezone(),
                is_stale=True,
                is_trading=False,
            )
            for price in (1, 2, 3, 4)
        ]


def test_cached_history_can_calculate_display_rsi_but_never_signal() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=CachedHistoryProvider(),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
    )

    snapshot = engine.tick()

    assert snapshot.rsi == 100
    assert snapshot.signal is SignalCondition.NONE
    assert snapshot.notification_status == "未发送（历史数据加载中）"
    assert notifier.messages == []


class OpeningAuctionProvider(ChartMarketDataProvider):
    def __init__(self) -> None:
        self.timestamp = datetime(2026, 8, 13, 9, 20)
        self.auction_bars = (
            MarketBar(self.timestamp, 4.1, 4.1, 4.1, 4.1, 100, 41_000),
        )

    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote(
            "TEST",
            4.1,
            self.timestamp,
            name="Test ETF",
            is_trading=False,
        )

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        return [
            MarketQuote("TEST", price, self.timestamp, is_trading=False)
            for price in (1, 2, 3, 4)
        ]

    def fetch_chart_series(self, period: str, limit: int) -> ChartSeries:
        return ChartSeries("TEST", "Test ETF", period, ())

    def get_opening_auction_bars(self, trading_date) -> tuple[MarketBar, ...]:
        return self.auction_bars


def test_opening_auction_is_visible_but_never_generates_signal() -> None:
    notifier = CollectingNotifier()
    engine = MonitorEngine(
        provider=OpeningAuctionProvider(),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=notifier,
        price_history_size=10,
    )

    snapshot = engine.tick()

    assert snapshot.rsi == 100
    assert snapshot.opening_auction_bars[0].close == 4.1
    assert snapshot.signal is SignalCondition.NONE
    assert snapshot.connection_status == "已连接（集合竞价）"
    assert snapshot.notification_status == "未发送（集合竞价不触发信号）"
    assert notifier.messages == []


class AuctionThenOpenProvider(OpeningAuctionProvider):
    def __init__(self) -> None:
        super().__init__()
        self._quotes = iter(
            (
                MarketQuote("TEST", 4.1, datetime(2026, 8, 13, 9, 20), is_trading=False),
                MarketQuote("TEST", 4.2, datetime(2026, 8, 13, 9, 21), is_trading=False),
                MarketQuote("TEST", 4.3, datetime(2026, 8, 13, 9, 30), is_trading=True),
            )
        )

    def fetch_latest_quote(self) -> MarketQuote:
        return next(self._quotes)


def test_auction_prices_do_not_become_extra_rsi_bars_after_market_opens() -> None:
    engine = MonitorEngine(
        provider=AuctionThenOpenProvider(),
        rsi_calculator=RsiCalculator(3),
        signal_detector=SignalDetector(30, 70),
        notifier=CollectingNotifier(),
        price_history_size=10,
        monitoring_period="1min",
    )

    auction_first = engine.tick()
    auction_second = engine.tick()
    market_open = engine.tick()

    assert auction_first.price_history == (1, 2, 3, 4.1)
    assert auction_second.price_history == (1, 2, 3, 4.2)
    assert market_open.price_history == (1, 2, 3, 4.3)
