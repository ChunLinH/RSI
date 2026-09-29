from dataclasses import dataclass
import json
import logging
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rsi_monitor.market_data.base import MarketDataError


@dataclass(frozen=True)
class SecuritySearchResult:
    symbol: str
    name: str
    market: str


class TencentSecuritySearcher:
    """通过腾讯证券搜索接口将中文名称匹配为 A 股或 ETF 代码。"""

    _URL = "https://smartbox.gtimg.cn/s3/"

    def __init__(
        self,
        timeout_seconds: float,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._logger = logging.getLogger(__name__)

    def search(self, query: str) -> list[SecuritySearchResult]:
        query = query.strip()
        if not query:
            return []

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                request = Request(
                    f"{self._URL}?{urlencode({'q': query, 't': 'all'})}",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://gu.qq.com/",
                    },
                )
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    if not 200 <= response.status < 300:
                        raise MarketDataError(f"证券搜索接口 HTTP 状态码：{response.status}")
                    payload = response.read().decode("utf-8")
                return self._parse_response(payload)
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
                        "证券名称搜索失败（第 %s/%s 次）：%s；将在 %s 秒后重试",
                        attempt,
                        self._max_retries,
                        error,
                        self._retry_delay_seconds,
                    )
                    time.sleep(self._retry_delay_seconds)
        raise MarketDataError(
            f"证券名称搜索失败，已重试 {self._max_retries} 次：{last_error}"
        ) from last_error

    @classmethod
    def _parse_response(cls, payload: str) -> list[SecuritySearchResult]:
        match = re.search(r'v_hint="([^"]*)"', payload)
        if match is None:
            raise MarketDataError("证券搜索返回格式无效")
        encoded_results = match.group(1)
        decoded_results = json.loads(f'"{encoded_results}"')

        results: list[SecuritySearchResult] = []
        seen_symbols: set[str] = set()
        for item in decoded_results.split("^"):
            fields = item.split("~")
            if len(fields) < 3:
                continue
            market = fields[0].strip().lower()
            symbol = fields[1].strip()
            name = fields[2].strip()
            if (
                market not in {"sh", "sz"}
                or re.fullmatch(r"\d{6}", symbol) is None
                or not name
                or symbol in seen_symbols
            ):
                continue
            seen_symbols.add(symbol)
            results.append(SecuritySearchResult(symbol, name, market))
        return results
