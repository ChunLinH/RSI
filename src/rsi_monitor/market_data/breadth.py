from dataclasses import dataclass
from datetime import date, datetime
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from rsi_monitor.market_data.base import MarketDataError
from rsi_monitor.market_data.tencent_china import TencentChinaMarketDataProvider


@dataclass(frozen=True)
class MarketBreadth:
    name: str
    up_count: int
    down_count: int
    flat_count: int
    change_percent: float | None
    timestamp: datetime | None = None
    turnover_amount: float | None = None
    previous_same_time_amount: float | None = None


class EastmoneyMarketBreadthProvider:
    """获取沪深京涨跌家数，以及可精确同比的沪深成交额。"""

    _URL = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get?"
        "fltt=2&invt=2&fields=f6,f12,f14,f3,f104,f105,f106,f124&"
        "secids=47.800000,47.800004"
    )
    _FALLBACK_URL = _URL.replace("push2.eastmoney.com", "push2delay.eastmoney.com")

    def __init__(
        self,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._timezone = ZoneInfo("Asia/Shanghai")
        self._exchange_index_providers = tuple(
            TencentChinaMarketDataProvider(
                symbol,
                timeout_seconds,
                max_retries,
                retry_delay_seconds,
            )
            for symbol in ("000001", "399001")
        )
        self._comparison_cache_key: tuple[date, int, int] | None = None
        self._comparison_cache_amount: float | None = None

    def fetch_all_a(self) -> MarketBreadth:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._fetch_all_a_once()
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
                    time.sleep(self._retry_delay_seconds)
        raise MarketDataError(f"获取全 A 涨跌家数失败：{last_error}") from last_error

    def _fetch_all_a_once(self) -> MarketBreadth:
        try:
            payload = self._request_json(self._URL, "全 A 涨跌家数")
        except (
            HTTPError,
            URLError,
            OSError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            MarketDataError,
        ):
            payload = self._request_json(self._FALLBACK_URL, "全 A 涨跌家数备用")
        if payload.get("rc") != 0:
            raise MarketDataError(str(payload.get("rt") or "全 A 涨跌家数接口返回错误"))
        try:
            items = {
                str(item["f12"]): item
                for item in payload["data"]["diff"]
            }
            breadth_item = items["800004"]
            turnover_item = items["800000"]
            timestamp = self._timestamp_from_epoch(breadth_item.get("f124"))
            turnover_amount = self._optional_float(turnover_item.get("f6"))
            try:
                previous_same_time_amount = self._fetch_previous_same_time_amount(timestamp)
            except (
                HTTPError,
                URLError,
                OSError,
                TimeoutError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                MarketDataError,
            ):
                previous_same_time_amount = None
            return MarketBreadth(
                name="沪深京全A",
                up_count=int(breadth_item["f104"]),
                down_count=int(breadth_item["f105"]),
                flat_count=int(breadth_item["f106"]),
                change_percent=self._optional_float(breadth_item.get("f3")),
                timestamp=timestamp,
                turnover_amount=turnover_amount,
                previous_same_time_amount=previous_same_time_amount,
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise MarketDataError("全 A 涨跌家数格式无效") from error

    def _fetch_previous_same_time_amount(
        self, current_timestamp: datetime | None
    ) -> float | None:
        if current_timestamp is None:
            return None
        cache_key = (
            current_timestamp.date(),
            current_timestamp.hour,
            current_timestamp.minute,
        )
        if cache_key == self._comparison_cache_key:
            return self._comparison_cache_amount
        total = 0.0
        for provider in self._exchange_index_providers:
            series = provider.fetch_chart_series("five_day", 2_000)
            previous_dates = sorted(
                {
                    bar.timestamp.date()
                    for bar in series.bars
                    if bar.timestamp.date() < current_timestamp.date()
                }
            )
            if not previous_dates:
                return None
            previous_date = previous_dates[-1]
            amounts = [
                bar.amount
                for bar in series.bars
                if bar.timestamp.date() == previous_date
                and bar.timestamp.time() <= current_timestamp.time()
                and bar.amount is not None
            ]
            if not amounts:
                return None
            total += sum(amounts)
        self._comparison_cache_key = cache_key
        self._comparison_cache_amount = total
        return total

    def _request_json(self, url: str, description: str) -> dict:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            },
        )
        with urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise MarketDataError(f"{description}接口 HTTP 状态码：{response.status}")
            return json.loads(response.read().decode("utf-8"))

    def _timestamp_from_epoch(self, value: object) -> datetime | None:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return None
        if timestamp <= 0:
            return None
        return datetime.fromtimestamp(timestamp, self._timezone)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
