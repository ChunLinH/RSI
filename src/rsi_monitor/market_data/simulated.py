from datetime import datetime
import random

from rsi_monitor.market_data.base import MarketDataProvider, MarketQuote


class SimulatedMarketDataProvider(MarketDataProvider):
    """用于演示的确定性波段行情：持续上涨后持续下跌。"""

    def __init__(
        self,
        symbol: str,
        initial_price: float,
        step_percent: float,
        trend_length: int,
        random_seed: int,
    ) -> None:
        self._symbol = symbol
        self._price = initial_price
        self._step_percent = step_percent
        self._trend_length = trend_length
        self._random = random.Random(random_seed)
        self._tick_count = 0

    def fetch_latest_quote(self) -> MarketQuote:
        direction = 1 if (self._tick_count // self._trend_length) % 2 == 0 else -1
        noise = self._random.uniform(-0.1, 0.1) * self._step_percent
        change = direction * self._step_percent + noise
        self._price = max(0.01, self._price * (1 + change))
        self._tick_count += 1
        return MarketQuote(
            symbol=self._symbol,
            price=round(self._price, 4),
            timestamp=datetime.now().astimezone(),
        )
