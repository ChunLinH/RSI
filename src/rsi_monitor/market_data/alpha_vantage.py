from datetime import datetime
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from rsi_monitor.market_data.base import HistoricalMarketDataProvider, MarketDataError, MarketQuote


class AlphaVantageMarketDataProvider(HistoricalMarketDataProvider):
    """通过 Alpha Vantage 获取股票和 ETF 的分钟收盘价。"""

    def __init__(
        self,
        symbol: str,
        interval: str,
        api_key: str,
        base_url: str,
        entitlement: str,
        timezone: str,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self._symbol = symbol.upper()
        self._interval = interval
        self._api_key = api_key
        self._base_url = base_url
        self._entitlement = entitlement
        self._timezone = ZoneInfo(timezone)
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._cached_quotes: list[MarketQuote] = []
        self._name = self._symbol
        self._name_requested = False
        self._logger = logging.getLogger(__name__)

    def fetch_latest_quote(self) -> MarketQuote:
        if not self._api_key:
            raise MarketDataError("未配置 Alpha Vantage API Key")
        self._cached_quotes = self._fetch_intraday_quotes()
        self._load_name_once()
        return self._cached_quotes[-1]

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        if not self._cached_quotes:
            self.fetch_latest_quote()
        return self._cached_quotes[-limit:]

    def _fetch_intraday_quotes(self) -> list[MarketQuote]:
        payload = self._request(
            {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": self._symbol,
                "interval": self._interval,
                "adjusted": "false",
                "extended_hours": "false",
                "outputsize": "compact",
                "entitlement": self._entitlement,
            }
        )
        series_key = f"Time Series ({self._interval})"
        series = payload.get(series_key)
        if not isinstance(series, dict) or not series:
            raise MarketDataError(f"Alpha Vantage 响应缺少 {series_key}")

        quotes: list[MarketQuote] = []
        for value_time, values in series.items():
            try:
                timestamp = datetime.strptime(value_time, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=self._timezone
                )
                quotes.append(
                    MarketQuote(
                        symbol=self._symbol,
                        name=self._name,
                        price=float(values["4. close"]),
                        timestamp=timestamp,
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise MarketDataError("Alpha Vantage 返回了无效的分钟行情") from error
        return sorted(quotes, key=lambda quote: quote.timestamp)

    def _load_name_once(self) -> None:
        if self._name_requested:
            return
        self._name_requested = True
        for function in ("OVERVIEW", "ETF_PROFILE"):
            try:
                payload = self._request({"function": function, "symbol": self._symbol})
            except MarketDataError as error:
                self._logger.warning("获取 %s 名称失败：%s", self._symbol, error)
                return
            name = payload.get("Name") or payload.get("name")
            if isinstance(name, str) and name.strip():
                self._name = name.strip()
                self._cached_quotes = [
                    MarketQuote(
                        symbol=quote.symbol,
                        name=self._name,
                        price=quote.price,
                        timestamp=quote.timestamp,
                    )
                    for quote in self._cached_quotes
                ]
                return

    def _request(self, parameters: dict[str, str]) -> dict[str, object]:
        request_parameters = {**parameters, "apikey": self._api_key}
        url = f"{self._base_url}?{urlencode(request_parameters)}"
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                request = Request(url, headers={"User-Agent": "stock-rsi-monitor/0.1"})
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    if not 200 <= response.status < 300:
                        raise MarketDataError(f"行情接口 HTTP 状态码：{response.status}")
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise MarketDataError("行情接口返回格式无效")
                self._raise_if_api_error(payload)
                return payload
            except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError, MarketDataError) as error:
                last_error = error
                if attempt < self._max_retries:
                    self._logger.warning(
                        "%s %s 行情请求失败（第 %s/%s 次）：%s；将在 %s 秒后重试",
                        self._symbol,
                        self._interval,
                        attempt,
                        self._max_retries,
                        error,
                        self._retry_delay_seconds,
                    )
                    time.sleep(self._retry_delay_seconds)
        raise MarketDataError(
            f"获取 {self._symbol} {self._interval} 行情失败，已重试 {self._max_retries} 次：{last_error}"
        )

    @staticmethod
    def _raise_if_api_error(payload: dict[str, object]) -> None:
        for key in ("Error Message", "Note", "Information"):
            message = payload.get(key)
            if isinstance(message, str) and message:
                raise MarketDataError(message)
