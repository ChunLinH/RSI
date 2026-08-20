from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time as clock_time
import json
import logging
from math import isfinite
from pathlib import Path
import re
import time
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from rsi_monitor.market_data.base import (
    ChartMarketDataProvider,
    ChartSeries,
    MarketBar,
    MarketDataError,
    MarketQuote,
)
from rsi_monitor.market_data.cache import MarketDataCache
from rsi_monitor.market_data.chart_periods import CHART_PERIODS


ResultT = TypeVar("ResultT")


class TencentChinaMarketDataProvider(ChartMarketDataProvider):
    """获取中国 A 股/ETF 实时价、分钟线和前复权长周期 K 线。"""

    _QUOTE_URL = "https://qt.gtimg.cn/q="
    _MINUTE_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    _LONG_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _INTRADAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    _FIVE_DAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"

    def __init__(
        self,
        symbol: str,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float,
        interval: str = "1min",
        cache_directory: Path | None = None,
    ) -> None:
        if not re.fullmatch(r"\d{6}", symbol.strip()):
            raise ValueError("中国 A 股和 ETF 代码必须是六位数字")
        if interval not in {"1min", "5min", "15min"}:
            raise ValueError("仅支持 1min、5min、15min 监控周期")
        self._symbol = symbol.strip()
        self._market_symbol = self._to_market_symbol(self._symbol)
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._interval = interval
        self._timezone = ZoneInfo("Asia/Shanghai")
        self._logger = logging.getLogger(__name__)
        self._cache = MarketDataCache(cache_directory) if cache_directory is not None else None
        self._next_quote_cache_at = 0.0
        self._auction_bars: tuple[MarketBar, ...] = ()

    def fetch_latest_quote(self) -> MarketQuote:
        try:
            quote = self._retry(self._fetch_quote_once, "实时行情")
        except MarketDataError:
            cached = self._cache.load_quote(self._symbol) if self._cache is not None else None
            if cached is None:
                raise
            self._logger.warning("%s 实时行情不可用，显示本地缓存", self._symbol)
            return cached
        if self._cache is not None and time.monotonic() >= self._next_quote_cache_at:
            self._cache.save_quote(quote)
            self._next_quote_cache_at = time.monotonic() + 30
        self._record_auction_quote(quote)
        return quote

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        """返回监控 RSI 初始化所需的分钟收盘价。"""
        series = self.fetch_chart_series(self._interval, limit)
        return [
            MarketQuote(
                symbol=series.symbol,
                name=series.name,
                price=bar.close,
                timestamp=bar.timestamp,
                is_stale=series.is_stale,
                is_trading=False,
            )
            for bar in series.bars[-limit:]
        ]

    def fetch_chart_series(self, period: str, limit: int) -> ChartSeries:
        if period not in CHART_PERIODS:
            raise ValueError(f"不支持的图表周期：{period}")
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        try:
            series = self._retry(
                lambda: self._fetch_chart_series_once(period, limit),
                f"{period} K 线",
            )
        except MarketDataError:
            cached = (
                self._cache.load_series(self._symbol, period)
                if self._cache is not None
                else None
            )
            if cached is None:
                raise
            self._logger.warning("%s %s 行情不可用，显示本地缓存", self._symbol, period)
            return self._attach_auction_history(cached)
        if self._cache is not None:
            self._cache.save_series(series)
        return self._attach_auction_history(series)

    def _retry(self, operation: Callable[[], ResultT], description: str) -> ResultT:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return operation()
            except (
                HTTPError,
                URLError,
                OSError,
                TimeoutError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                MarketDataError,
            ) as error:
                last_error = error
                if attempt < self._max_retries:
                    self._logger.warning(
                        "%s %s请求失败（第 %s/%s 次）：%s；将在 %s 秒后重试",
                        self._symbol,
                        description,
                        attempt,
                        self._max_retries,
                        error,
                        self._retry_delay_seconds,
                    )
                    time.sleep(self._retry_delay_seconds)
        raise MarketDataError(
            f"获取 {self._symbol} {description}失败，已重试 {self._max_retries} 次：{last_error}"
        ) from last_error

    def _fetch_quote_once(self) -> MarketQuote:
        request = Request(
            f"{self._QUOTE_URL}{self._market_symbol}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise MarketDataError(f"行情接口 HTTP 状态码：{response.status}")
            payload = response.read().decode("gbk")
        fields = self._parse_quote_payload(payload)
        try:
            name = fields[1].strip()
            price = float(fields[3])
        except (IndexError, ValueError) as error:
            raise MarketDataError(f"{self._symbol} 的实时行情格式无效") from error
        if not name or not isfinite(price) or price <= 0:
            raise MarketDataError(f"{self._symbol} 的实时行情不可用")
        try:
            previous_close = float(fields[4])
        except (IndexError, ValueError):
            previous_close = 0.0
        change_percent = None
        if isfinite(previous_close) and previous_close > 0:
            change_percent = (price - previous_close) / previous_close * 100
        timestamp = self._extract_timestamp(fields)
        cumulative_volume = self._optional_float(fields[6] if len(fields) > 6 else None)
        cumulative_amount = self._quote_amount(fields)
        return MarketQuote(
            symbol=self._symbol,
            name=name,
            price=price,
            timestamp=timestamp,
            change_percent=change_percent,
            is_trading=self._is_live_trading_quote(timestamp),
            cumulative_volume=cumulative_volume,
            cumulative_amount=cumulative_amount,
            previous_close=previous_close if previous_close > 0 else None,
            open_price=self._field_float(fields, 5),
            high_price=self._field_float(fields, 33),
            low_price=self._field_float(fields, 34),
            price_change=self._field_float(fields, 31),
            turnover_rate=self._field_float(fields, 38),
            amplitude=self._field_float(fields, 43),
            volume_ratio=self._field_float(fields, 49),
            average_price=self._field_float(fields, 51),
            circulating_market_cap=self._field_float(fields, 44),
            total_market_cap=self._field_float(fields, 45),
            pe_ratio=self._field_float(fields, 39),
            pb_ratio=self._field_float(fields, 46),
            inside_volume=self._field_float(fields, 8),
            outside_volume=self._field_float(fields, 7),
            upper_limit=self._field_float(fields, 47),
            lower_limit=self._field_float(fields, 48),
            bid_levels=self._price_levels(fields, 9),
            ask_levels=self._price_levels(fields, 19),
        )

    def _record_auction_quote(self, quote: MarketQuote) -> None:
        if not self._is_opening_auction_time(quote.timestamp):
            return
        auction_bars = self._auction_bars_for_date(quote.timestamp.date())
        if auction_bars and auction_bars[-1].timestamp == quote.timestamp:
            auction_bars = auction_bars[:-1]
        previous_volume = sum(bar.volume for bar in auction_bars)
        previous_amount = sum(bar.amount or 0.0 for bar in auction_bars)
        volume = max((quote.cumulative_volume or previous_volume) - previous_volume, 0.0)
        amount = max((quote.cumulative_amount or previous_amount) - previous_amount, 0.0)
        auction_bars = (
            *auction_bars,
            MarketBar(
                timestamp=quote.timestamp,
                open=quote.price,
                high=quote.price,
                low=quote.price,
                close=quote.price,
                volume=volume,
                amount=amount,
            ),
        )
        self._auction_bars = auction_bars
        if self._cache is not None:
            self._cache.save_series(
                ChartSeries(
                    symbol=self._symbol,
                    name=quote.name or self._symbol,
                    period="auction",
                    bars=auction_bars,
                )
            )

    def _attach_auction_history(self, series: ChartSeries) -> ChartSeries:
        if series.period != "intraday" or not series.bars:
            return series
        trading_date = series.bars[-1].timestamp.date()
        auction_bars = self._auction_bars_for_date(trading_date)
        return replace(series, auction_bars=auction_bars)

    def _auction_bars_for_date(self, trading_date) -> tuple[MarketBar, ...]:
        bars = tuple(
            bar for bar in self._auction_bars if bar.timestamp.date() == trading_date
        )
        if bars or self._cache is None:
            return bars
        cached = self._cache.load_series(self._symbol, "auction")
        if cached is None:
            return ()
        bars = tuple(
            bar for bar in cached.bars if bar.timestamp.date() == trading_date
        )
        self._auction_bars = bars
        return bars

    def get_opening_auction_bars(self, trading_date) -> tuple[MarketBar, ...]:
        return self._auction_bars_for_date(trading_date)

    @staticmethod
    def _is_opening_auction_time(timestamp: datetime) -> bool:
        return clock_time(9, 15) <= timestamp.time() <= clock_time(9, 25)

    @staticmethod
    def _quote_amount(fields: list[str]) -> float | None:
        if len(fields) <= 35:
            return None
        components = fields[35].split("/")
        return TencentChinaMarketDataProvider._optional_float(
            components[2] if len(components) > 2 else None
        )

    @staticmethod
    def _field_float(fields: list[str], index: int) -> float | None:
        return TencentChinaMarketDataProvider._optional_float(
            fields[index] if len(fields) > index else None
        )

    @staticmethod
    def _price_levels(
        fields: list[str], start_index: int
    ) -> tuple[tuple[float, float], ...]:
        levels: list[tuple[float, float]] = []
        for index in range(start_index, start_index + 10, 2):
            price = TencentChinaMarketDataProvider._field_float(fields, index)
            volume = TencentChinaMarketDataProvider._field_float(fields, index + 1)
            if price is not None and price > 0 and volume is not None:
                levels.append((price, volume))
        return tuple(levels)

    def _fetch_chart_series_once(self, period: str, limit: int) -> ChartSeries:
        if period == "intraday":
            return self._fetch_intraday_series()
        if period == "five_day":
            return self._fetch_five_day_series()
        if period in {"1min", "5min", "15min", "30min", "60min"}:
            return self._fetch_minute_kline_series(period, limit)
        if period == "120min":
            source = self._fetch_minute_kline_series("60min", min(limit * 2, 320))
            return replace(source, period=period, bars=self._aggregate_pairs(source.bars)[-limit:])
        if period in {"day", "week", "month"}:
            return self._fetch_long_kline_series(period, limit)
        source = self._fetch_long_kline_series("month", min(limit * 12, 600))
        return replace(source, period="year", bars=self._aggregate_years(source.bars)[-limit:])

    def _fetch_minute_kline_series(self, period: str, limit: int) -> ChartSeries:
        minute_key = f"m{period.removesuffix('min')}"
        parameters = {"param": f"{self._market_symbol},{minute_key},,{limit}"}
        payload = self._request_json(f"{self._MINUTE_KLINE_URL}?{urlencode(parameters)}")
        node = self._payload_node(payload)
        entries = node.get(minute_key)
        if not isinstance(entries, list):
            raise MarketDataError(f"{self._symbol} 未返回 {period} K 线")
        bars = tuple(self._parse_minute_kline_entry(entry) for entry in entries)
        return self._create_series(node, period, bars[-limit:])

    def _fetch_long_kline_series(self, period: str, limit: int) -> ChartSeries:
        parameters = {"param": f"{self._market_symbol},{period},,,{limit},qfq"}
        payload = self._request_json(f"{self._LONG_KLINE_URL}?{urlencode(parameters)}")
        node = self._payload_node(payload)
        entries = node.get(f"qfq{period}", node.get(period))
        if not isinstance(entries, list):
            raise MarketDataError(f"{self._symbol} 未返回 {period} K 线")
        bars = tuple(self._parse_long_kline_entry(entry) for entry in entries)
        return self._create_series(node, period, bars[-limit:])

    def _fetch_intraday_series(self) -> ChartSeries:
        payload = self._request_json(
            f"{self._INTRADAY_URL}?{urlencode({'code': self._market_symbol})}"
        )
        node = self._payload_node(payload)
        data = node.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise MarketDataError(f"{self._symbol} 未返回分时数据")
        date_text = str(data.get("date", ""))
        bars = self._parse_trend_entries(date_text, data["data"])
        return self._create_series(node, "intraday", bars)

    def _fetch_five_day_series(self) -> ChartSeries:
        payload = self._request_json(
            f"{self._FIVE_DAY_URL}?{urlencode({'code': self._market_symbol})}"
        )
        node = self._payload_node(payload)
        days = node.get("data")
        if not isinstance(days, list):
            raise MarketDataError(f"{self._symbol} 未返回五日分时数据")
        bars: list[MarketBar] = []
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("data"), list):
                continue
            bars.extend(self._parse_trend_entries(str(day.get("date", "")), day["data"]))
        if not bars:
            raise MarketDataError(f"{self._symbol} 五日分时数据为空")
        return self._create_series(node, "five_day", tuple(sorted(bars, key=lambda bar: bar.timestamp)))

    def _request_json(self, url: str) -> dict:
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise MarketDataError(f"K 线接口 HTTP 状态码：{response.status}")
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != 0:
            raise MarketDataError(str(payload.get("msg") or "K 线接口返回错误"))
        return payload

    def _payload_node(self, payload: dict) -> dict:
        try:
            node = payload["data"][self._market_symbol]
        except (KeyError, TypeError) as error:
            raise MarketDataError(f"{self._symbol} 的 K 线格式无效") from error
        if not isinstance(node, dict):
            raise MarketDataError(f"{self._symbol} 的 K 线不可用")
        return node

    def _create_series(
        self,
        node: dict,
        period: str,
        bars: tuple[MarketBar, ...],
    ) -> ChartSeries:
        if not bars:
            raise MarketDataError(f"{self._symbol} {period} 数据为空")
        previous_close = self._optional_float(node.get("prec"))
        return ChartSeries(
            symbol=self._symbol,
            name=self._name_from_node(node) or self._symbol,
            period=period,
            bars=bars,
            previous_close=previous_close,
        )

    def _parse_minute_kline_entry(self, entry: object) -> MarketBar:
        try:
            timestamp, open_, close, high, low, volume, *_ = entry
            return MarketBar(
                timestamp=datetime.strptime(str(timestamp), "%Y%m%d%H%M").replace(
                    tzinfo=self._timezone
                ),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
            )
        except (TypeError, ValueError) as error:
            raise MarketDataError(f"{self._symbol} 的分钟 K 线格式无效") from error

    def _parse_long_kline_entry(self, entry: object) -> MarketBar:
        try:
            timestamp, open_, close, high, low, volume, *_ = entry
            return MarketBar(
                timestamp=datetime.strptime(str(timestamp), "%Y-%m-%d").replace(
                    tzinfo=self._timezone
                ),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
            )
        except (TypeError, ValueError) as error:
            raise MarketDataError(f"{self._symbol} 的长周期 K 线格式无效") from error

    def _parse_trend_entries(self, date_text: str, entries: list) -> tuple[MarketBar, ...]:
        try:
            trading_date = datetime.strptime(date_text, "%Y%m%d").date()
        except ValueError as error:
            raise MarketDataError(f"{self._symbol} 的分时日期无效") from error
        bars: list[MarketBar] = []
        previous_volume = 0.0
        previous_amount = 0.0
        for entry in entries:
            try:
                time_text, price_text, cumulative_volume_text, cumulative_amount_text = str(
                    entry
                ).split()[:4]
                if time_text > "1500":
                    continue
                timestamp = datetime.combine(
                    trading_date,
                    datetime.strptime(time_text, "%H%M").time(),
                    self._timezone,
                )
                price = float(price_text)
                cumulative_volume = float(cumulative_volume_text)
                cumulative_amount = float(cumulative_amount_text)
            except (TypeError, ValueError) as error:
                raise MarketDataError(f"{self._symbol} 的分时数据格式无效") from error
            volume = max(cumulative_volume - previous_volume, 0.0)
            amount = max(cumulative_amount - previous_amount, 0.0)
            previous_volume = cumulative_volume
            previous_amount = cumulative_amount
            bars.append(MarketBar(timestamp, price, price, price, price, volume, amount))
        return tuple(bars)

    @staticmethod
    def _aggregate_pairs(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
        aggregated: list[MarketBar] = []
        current_date = None
        day_bars: list[MarketBar] = []
        for bar in bars:
            if current_date is not None and bar.timestamp.date() != current_date:
                aggregated.extend(TencentChinaMarketDataProvider._pair_day(day_bars))
                day_bars = []
            current_date = bar.timestamp.date()
            day_bars.append(bar)
        aggregated.extend(TencentChinaMarketDataProvider._pair_day(day_bars))
        return tuple(aggregated)

    @staticmethod
    def _pair_day(bars: list[MarketBar]) -> list[MarketBar]:
        result: list[MarketBar] = []
        for index in range(0, len(bars), 2):
            group = bars[index : index + 2]
            result.append(TencentChinaMarketDataProvider._merge_bars(group))
        return result

    @staticmethod
    def _aggregate_years(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
        groups: dict[int, list[MarketBar]] = {}
        for bar in bars:
            groups.setdefault(bar.timestamp.year, []).append(bar)
        return tuple(
            TencentChinaMarketDataProvider._merge_bars(groups[year])
            for year in sorted(groups)
        )

    @staticmethod
    def _merge_bars(bars: list[MarketBar]) -> MarketBar:
        first, last = bars[0], bars[-1]
        amounts = [bar.amount for bar in bars if bar.amount is not None]
        return MarketBar(
            timestamp=last.timestamp,
            open=first.open,
            high=max(bar.high for bar in bars),
            low=min(bar.low for bar in bars),
            close=last.close,
            volume=sum(bar.volume for bar in bars),
            amount=sum(amounts) if amounts else None,
        )

    def _name_from_node(self, node: dict) -> str:
        try:
            return str(node["qt"][self._market_symbol][1]).strip()
        except (KeyError, IndexError, TypeError):
            return ""

    def _is_live_trading_quote(self, timestamp: datetime) -> bool:
        now = datetime.now(self._timezone)
        if timestamp.date() != now.date() or now.weekday() >= 5:
            return False
        current = now.time()
        in_session = (
            clock_time(9, 30) <= current <= clock_time(11, 30)
            or clock_time(13, 0) <= current <= clock_time(15, 0)
        )
        return in_session and abs((now - timestamp).total_seconds()) <= 5 * 60

    def _extract_timestamp(self, fields: list[str]) -> datetime:
        for value in fields:
            if re.fullmatch(r"\d{14}", value):
                return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=self._timezone)
        return datetime.now(self._timezone)

    def _parse_quote_payload(self, payload: str) -> list[str]:
        match = re.search(r'="(.*)";', payload)
        if not match:
            raise MarketDataError(f"未找到 {self._symbol} 的实时行情")
        fields = match.group(1).split("~")
        if len(fields) < 4:
            raise MarketDataError(f"{self._symbol} 的实时行情字段不足")
        return fields

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            result = float(value)
            return result if isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_market_symbol(symbol: str) -> str:
        if symbol == "000001":
            return "sh000001"
        return f"sh{symbol}" if symbol.startswith(("5", "6", "9")) else f"sz{symbol}"
