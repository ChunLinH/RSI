from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from rsi_monitor.market_data.base import MarketDataError
from rsi_monitor.market_data.tencent_china import TencentChinaMarketDataProvider


@dataclass(frozen=True)
class MarketBreadth:
    name: str
    up_count: int | None
    down_count: int | None
    flat_count: int | None
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
    # The normal Eastmoney push hosts can be unavailable on some networks.
    # The test host serves the same public payload and is kept as a last resort.
    _TEST_URL = _URL.replace("push2.eastmoney.com", "push2test.eastmoney.com")

    def __init__(
        self,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float,
        cache_directory: Path | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._timezone = ZoneInfo("Asia/Shanghai")
        comparison_timeout = min(timeout_seconds, 3.0)
        self._exchange_index_providers = tuple(
            TencentChinaMarketDataProvider(
                symbol,
                comparison_timeout,
                1,
                0,
            )
            for symbol in ("000001", "399001")
        )
        self._comparison_cache_key: tuple[date, int, int] | None = None
        self._comparison_cache_amount: float | None = None
        self._breadth_cache_path = (
            None
            if cache_directory is None
            else cache_directory / "market_breadth.json"
        )
        self._last_breadth = self._load_cached_breadth()

    def fetch_all_a(self) -> MarketBreadth:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                breadth = self._fetch_all_a_once()
                self._last_breadth = breadth
                self._save_cached_breadth(breadth)
                return breadth
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
        if self._last_breadth is not None:
            return self._last_breadth
        raise MarketDataError(f"获取全 A 涨跌家数失败：{last_error}") from last_error

    def _load_cached_breadth(self) -> MarketBreadth | None:
        if self._breadth_cache_path is None or not self._breadth_cache_path.exists():
            return None
        try:
            payload = json.loads(self._breadth_cache_path.read_text(encoding="utf-8"))
            timestamp = payload.get("timestamp")
            return MarketBreadth(
                name=str(payload.get("name") or "沪深京全A"),
                up_count=self._optional_int(payload.get("up_count")),
                down_count=self._optional_int(payload.get("down_count")),
                flat_count=self._optional_int(payload.get("flat_count")),
                change_percent=self._optional_float(payload.get("change_percent")),
                timestamp=(
                    None if timestamp is None else datetime.fromisoformat(str(timestamp))
                ),
                turnover_amount=self._optional_float(payload.get("turnover_amount")),
                previous_same_time_amount=self._optional_float(
                    payload.get("previous_same_time_amount")
                ),
            )
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _save_cached_breadth(self, breadth: MarketBreadth) -> None:
        if self._breadth_cache_path is None:
            return
        payload = {
            "name": breadth.name,
            "up_count": breadth.up_count,
            "down_count": breadth.down_count,
            "flat_count": breadth.flat_count,
            "change_percent": breadth.change_percent,
            "timestamp": None if breadth.timestamp is None else breadth.timestamp.isoformat(),
            "turnover_amount": breadth.turnover_amount,
            "previous_same_time_amount": breadth.previous_same_time_amount,
        }
        try:
            self._breadth_cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._breadth_cache_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_path.replace(self._breadth_cache_path)
        except OSError:
            return

    def _fetch_all_a_once(self) -> MarketBreadth:
        last_error: Exception | None = None
        for url, description in (
            (self._URL, "全 A 涨跌家数"),
            (self._FALLBACK_URL, "全 A 涨跌家数备用"),
            (self._TEST_URL, "全 A 涨跌家数测试备用"),
        ):
            try:
                payload = self._request_json(url, description)
                if payload.get("rc") != 0:
                    raise MarketDataError(
                        str(payload.get("rt") or "全 A 涨跌家数接口返回错误")
                    )
                return self._parse_breadth_payload(payload)
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
        raise MarketDataError(str(last_error or "全 A 涨跌家数接口不可用")) from last_error

    def _parse_breadth_payload(self, payload: dict) -> MarketBreadth:
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
        try:
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
        except (HTTPError, URLError, OSError, TimeoutError, MarketDataError):
            return None
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

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
