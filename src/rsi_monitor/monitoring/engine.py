from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as clock_time
import logging
import re
import time

from rsi_monitor.indicators.rsi import RsiCalculator
from rsi_monitor.market_data.base import (
    ChartMarketDataProvider,
    ChartSeries,
    HistoricalMarketDataProvider,
    MarketDataError,
    MarketDataProvider,
    MarketBar,
    MarketQuote,
)
from rsi_monitor.notifications.base import NotificationProvider
from rsi_monitor.notifications.formatters import create_signal_notification
from rsi_monitor.signals.evaluator import SignalCondition, SignalDetector, SignalEvent


@dataclass(frozen=True)
class MonitorSnapshot:
    quote: MarketQuote
    rsi: float | None
    rsi_values: dict[int, float | None]
    signal: SignalCondition
    notification_status: str
    connection_status: str
    price_history: tuple[float, ...]
    opening_auction_bars: tuple[MarketBar, ...] = ()


class MonitorEngine:
    """串联行情、指标、信号和通知的无界面业务流程。"""

    def __init__(
        self,
        provider: MarketDataProvider,
        rsi_calculator: RsiCalculator,
        signal_detector: SignalDetector,
        notifier: NotificationProvider,
        price_history_size: int,
        monitoring_period: str = "--",
        additional_rsi_calculators: dict[int, RsiCalculator] | None = None,
        history_retry_delay_seconds: float = 30,
    ) -> None:
        self._provider = provider
        self._rsi_calculator = rsi_calculator
        self._signal_rsi_period = rsi_calculator.period
        self._additional_rsi_calculators = additional_rsi_calculators or {}
        if self._signal_rsi_period in self._additional_rsi_calculators:
            raise ValueError("信号 RSI 周期不应重复提供")
        self._signal_detector = signal_detector
        self._notifier = notifier
        self._monitoring_period = monitoring_period
        self._prices: deque[float] = deque(maxlen=price_history_size)
        self._last_rsi: float | None = None
        self._history_initialized = False
        self._active_bar_start: datetime | None = None
        self._history_retry_delay_seconds = history_retry_delay_seconds
        self._next_history_retry_at = 0.0
        self._logger = logging.getLogger(__name__)

    def update_signal_thresholds(self, buy_threshold: float, sell_threshold: float) -> None:
        """更新当前监控项的阈值，且不会因更新操作立即产生通知。"""
        detector = SignalDetector(buy_threshold, sell_threshold)
        if self._last_rsi is not None:
            detector.evaluate(self._last_rsi)
        self._signal_detector = detector

    def tick(self) -> MonitorSnapshot:
        quote = self._provider.fetch_latest_quote()
        self._logger.debug("已获取 %s 行情：%.4f", quote.symbol, quote.price)
        history_ready = True
        if isinstance(self._provider, HistoricalMarketDataProvider):
            history_ready = self._update_historical_prices(quote)
        else:
            self._prices.append(quote.price)
        prices = list(self._prices)
        rsi = self._rsi_calculator.calculate(prices)
        rsi_values = {
            self._signal_rsi_period: rsi,
            **{
                period: calculator.calculate(prices)
                for period, calculator in self._additional_rsi_calculators.items()
            },
        }
        self._last_rsi = rsi
        may_signal = history_ready and quote.is_trading and not quote.is_stale
        if may_signal:
            event = self._signal_detector.evaluate(rsi)
            notification_status = "等待信号"
        else:
            event = SignalEvent(SignalCondition.NONE, False)
            if quote.is_stale:
                notification_status = "未发送（本地缓存）"
            elif not history_ready:
                notification_status = "未发送（历史数据加载中）"
            elif self._is_opening_auction_quote(quote):
                notification_status = "未发送（集合竞价不触发信号）"
            else:
                notification_status = "未发送（已收盘）"

        if event.should_notify:
            message = create_signal_notification(
                quote,
                rsi,
                self._monitoring_period,
                event.condition,
                rsi_period=self._signal_rsi_period,
            )
            try:
                result = self._notifier.send(message)
                notification_status = result.status
                if result.sent:
                    self._logger.info("已发送 %s %s 信号通知", quote.symbol, event.condition.value)
                else:
                    self._logger.warning(
                        "%s %s 信号未发送：%s", quote.symbol, event.condition.value, result.status
                    )
            except Exception as error:  # 通知故障不能中断行情监控。
                notification_status = f"发送失败：{error}"
                self._logger.exception("发送 %s 信号通知失败", quote.symbol)

        return MonitorSnapshot(
            quote=quote,
            rsi=rsi,
            rsi_values=rsi_values,
            signal=event.condition,
            notification_status=notification_status,
            connection_status=self._connection_status(quote, history_ready),
            price_history=tuple(prices),
            opening_auction_bars=(
                self._provider.get_opening_auction_bars(quote.timestamp.date())
                if isinstance(self._provider, ChartMarketDataProvider)
                else ()
            ),
        )

    def fetch_display_quote(self) -> MarketQuote:
        """获取仅用于界面展示的行情快照，不计算 RSI、信号或发送通知。"""
        return self._provider.fetch_latest_quote()

    def fetch_chart_series(self, period: str, limit: int) -> ChartSeries:
        if not isinstance(self._provider, ChartMarketDataProvider):
            raise MarketDataError("当前行情源不支持图表数据")
        return self._provider.fetch_chart_series(period, limit)

    def fetch_chart_series_before(
        self, period: str, before: datetime, limit: int
    ) -> ChartSeries | None:
        if not isinstance(self._provider, ChartMarketDataProvider):
            return None
        return self._provider.fetch_chart_series_before(period, before, limit)

    def _update_historical_prices(self, quote: MarketQuote) -> bool:
        if not self._history_initialized:
            if time.monotonic() < self._next_history_retry_at:
                self._update_active_bar_price(quote)
                return False
            try:
                recent_quotes = self._provider.fetch_recent_quotes(self._prices.maxlen)
            except MarketDataError as error:
                self._next_history_retry_at = (
                    time.monotonic() + self._history_retry_delay_seconds
                )
                self._logger.warning("%s 历史 K 线加载失败：%s", quote.symbol, error)
                self._update_active_bar_price(quote)
                return False
            self._prices.clear()
            self._prices.extend(item.price for item in recent_quotes)
            if any(item.is_stale for item in recent_quotes):
                self._next_history_retry_at = (
                    time.monotonic() + self._history_retry_delay_seconds
                )
                self._update_active_bar_price(quote)
                return False
            self._history_initialized = True
        self._update_active_bar_price(quote)
        return True

    @staticmethod
    def _connection_status(quote: MarketQuote, history_ready: bool) -> str:
        if quote.is_stale:
            return "本地缓存（行情连接失败）"
        if MonitorEngine._is_opening_auction_quote(quote):
            return "已连接（集合竞价）"
        if not quote.is_trading:
            return "已收盘（最后有效数据）"
        if not history_ready:
            return "已连接（历史 K 线重试中）"
        return "已连接"

    @staticmethod
    def _is_opening_auction_quote(quote: MarketQuote) -> bool:
        return clock_time(9, 15) <= quote.timestamp.time() <= clock_time(9, 25)

    def _update_active_bar_price(self, quote: MarketQuote) -> None:
        if self._is_opening_auction_quote(quote):
            if self._prices:
                self._prices[-1] = quote.price
            else:
                self._prices.append(quote.price)
            self._active_bar_start = None
            return
        bar_start = self._bar_start(quote.timestamp)
        if not self._prices:
            self._prices.append(quote.price)
            self._active_bar_start = bar_start
            return
        if self._active_bar_start is None or bar_start == self._active_bar_start:
            self._prices[-1] = quote.price
        elif bar_start > self._active_bar_start:
            self._prices.append(quote.price)
        else:
            self._logger.warning("忽略 %s 的过期实时行情", quote.symbol)
            return
        self._active_bar_start = bar_start

    def _bar_start(self, timestamp: datetime) -> datetime:
        match = re.fullmatch(r"(\d+)min", self._monitoring_period)
        minutes = int(match.group(1)) if match else 1
        return timestamp.replace(
            minute=timestamp.minute - timestamp.minute % minutes,
            second=0,
            microsecond=0,
        )
