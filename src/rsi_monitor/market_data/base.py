from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    price: float
    timestamp: datetime
    name: str = ""
    change_percent: float | None = None
    is_stale: bool = False
    is_trading: bool = True
    cumulative_volume: float | None = None
    cumulative_amount: float | None = None
    previous_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    price_change: float | None = None
    turnover_rate: float | None = None
    amplitude: float | None = None
    volume_ratio: float | None = None
    average_price: float | None = None
    circulating_market_cap: float | None = None
    total_market_cap: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    inside_volume: float | None = None
    outside_volume: float | None = None
    upper_limit: float | None = None
    lower_limit: float | None = None
    bid_levels: tuple[tuple[float, float], ...] = ()
    ask_levels: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class MarketBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None


@dataclass(frozen=True)
class ChartSeries:
    symbol: str
    name: str
    period: str
    bars: tuple[MarketBar, ...]
    previous_close: float | None = None
    is_stale: bool = False
    auction_bars: tuple[MarketBar, ...] = ()


class MarketDataError(RuntimeError):
    """行情数据不可用，调用方不得据此计算信号。"""


class MarketDataProvider(ABC):
    """行情来源接口；后续可替换为券商或行情服务实现。"""

    @abstractmethod
    def fetch_latest_quote(self) -> MarketQuote:
        """返回一条最新行情。"""


class HistoricalMarketDataProvider(MarketDataProvider):
    """可提供 RSI 初始化所需历史收盘价的可选行情接口。"""

    @abstractmethod
    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        """返回按时间升序排列的最近行情。"""


class ChartMarketDataProvider(HistoricalMarketDataProvider):
    """可按图表周期提供 OHLCV 数据的行情接口。"""

    @abstractmethod
    def fetch_chart_series(self, period: str, limit: int) -> ChartSeries:
        """返回按时间升序排列的图表数据。"""

    def get_opening_auction_bars(self, trading_date) -> tuple[MarketBar, ...]:
        """返回当天已采集的集合竞价快照；不支持时返回空序列。"""
        return ()
