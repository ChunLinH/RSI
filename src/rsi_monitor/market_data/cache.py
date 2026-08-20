from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path
import re

from rsi_monitor.market_data.base import ChartSeries, MarketBar, MarketQuote


class MarketDataCache:
    """将最后一次成功行情保存到用户数据目录，供休市或网络故障时显示。"""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._logger = logging.getLogger(__name__)

    def save_quote(self, quote: MarketQuote) -> None:
        self._write_json(
            self._quote_path(quote.symbol),
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "price": quote.price,
                "timestamp": quote.timestamp.isoformat(),
                "change_percent": quote.change_percent,
                "cumulative_volume": quote.cumulative_volume,
                "cumulative_amount": quote.cumulative_amount,
                "previous_close": quote.previous_close,
                "open_price": quote.open_price,
                "high_price": quote.high_price,
                "low_price": quote.low_price,
                "price_change": quote.price_change,
                "turnover_rate": quote.turnover_rate,
                "amplitude": quote.amplitude,
                "volume_ratio": quote.volume_ratio,
                "average_price": quote.average_price,
                "circulating_market_cap": quote.circulating_market_cap,
                "total_market_cap": quote.total_market_cap,
                "pe_ratio": quote.pe_ratio,
                "pb_ratio": quote.pb_ratio,
                "inside_volume": quote.inside_volume,
                "outside_volume": quote.outside_volume,
                "upper_limit": quote.upper_limit,
                "lower_limit": quote.lower_limit,
                "bid_levels": quote.bid_levels,
                "ask_levels": quote.ask_levels,
            },
        )

    def load_quote(self, symbol: str) -> MarketQuote | None:
        payload = self._read_json(self._quote_path(symbol))
        if payload is None:
            return None
        try:
            quote = MarketQuote(
                symbol=str(payload["symbol"]),
                name=str(payload.get("name", "")),
                price=float(payload["price"]),
                timestamp=datetime.fromisoformat(str(payload["timestamp"])),
                change_percent=(
                    None
                    if payload.get("change_percent") is None
                    else float(payload["change_percent"])
                ),
                cumulative_volume=(
                    None
                    if payload.get("cumulative_volume") is None
                    else float(payload["cumulative_volume"])
                ),
                cumulative_amount=(
                    None
                    if payload.get("cumulative_amount") is None
                    else float(payload["cumulative_amount"])
                ),
                previous_close=self._optional_float(payload, "previous_close"),
                open_price=self._optional_float(payload, "open_price"),
                high_price=self._optional_float(payload, "high_price"),
                low_price=self._optional_float(payload, "low_price"),
                price_change=self._optional_float(payload, "price_change"),
                turnover_rate=self._optional_float(payload, "turnover_rate"),
                amplitude=self._optional_float(payload, "amplitude"),
                volume_ratio=self._optional_float(payload, "volume_ratio"),
                average_price=self._optional_float(payload, "average_price"),
                circulating_market_cap=self._optional_float(
                    payload, "circulating_market_cap"
                ),
                total_market_cap=self._optional_float(payload, "total_market_cap"),
                pe_ratio=self._optional_float(payload, "pe_ratio"),
                pb_ratio=self._optional_float(payload, "pb_ratio"),
                inside_volume=self._optional_float(payload, "inside_volume"),
                outside_volume=self._optional_float(payload, "outside_volume"),
                upper_limit=self._optional_float(payload, "upper_limit"),
                lower_limit=self._optional_float(payload, "lower_limit"),
                bid_levels=self._levels(payload.get("bid_levels")),
                ask_levels=self._levels(payload.get("ask_levels")),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            self._logger.warning("忽略无效行情缓存 %s：%s", symbol, error)
            return None
        return replace(quote, is_stale=True, is_trading=False)

    @staticmethod
    def _optional_float(payload: dict, key: str) -> float | None:
        value = payload.get(key)
        return None if value is None else float(value)

    @staticmethod
    def _levels(value: object) -> tuple[tuple[float, float], ...]:
        if not isinstance(value, list):
            return ()
        return tuple((float(item[0]), float(item[1])) for item in value)

    def save_series(self, series: ChartSeries) -> None:
        self._write_json(
            self._series_path(series.symbol, series.period),
            {
                "symbol": series.symbol,
                "name": series.name,
                "period": series.period,
                "previous_close": series.previous_close,
                "bars": [
                    {
                        "timestamp": bar.timestamp.isoformat(),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "amount": bar.amount,
                    }
                    for bar in series.bars
                ],
                "auction_bars": [
                    {
                        "timestamp": bar.timestamp.isoformat(),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "amount": bar.amount,
                    }
                    for bar in series.auction_bars
                ],
            },
        )

    def load_series(self, symbol: str, period: str) -> ChartSeries | None:
        payload = self._read_json(self._series_path(symbol, period))
        if payload is None:
            return None
        try:
            bars = tuple(
                MarketBar(
                    timestamp=datetime.fromisoformat(str(item["timestamp"])),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(item["volume"]),
                    amount=None if item.get("amount") is None else float(item["amount"]),
                )
                for item in payload["bars"]
            )
            auction_bars = tuple(
                MarketBar(
                    timestamp=datetime.fromisoformat(str(item["timestamp"])),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(item["volume"]),
                    amount=None if item.get("amount") is None else float(item["amount"]),
                )
                for item in payload.get("auction_bars", [])
            )
            previous_close = payload.get("previous_close")
            return ChartSeries(
                symbol=str(payload["symbol"]),
                name=str(payload.get("name", "")),
                period=str(payload["period"]),
                bars=bars,
                previous_close=None if previous_close is None else float(previous_close),
                is_stale=True,
                auction_bars=auction_bars,
            )
        except (KeyError, TypeError, ValueError) as error:
            self._logger.warning("忽略无效 K 线缓存 %s %s：%s", symbol, period, error)
            return None

    def _quote_path(self, symbol: str) -> Path:
        return self._directory / f"{self._safe_key(symbol)}_quote.json"

    def _series_path(self, symbol: str, period: str) -> Path:
        return self._directory / f"{self._safe_key(symbol)}_{self._safe_key(period)}.json"

    @staticmethod
    def _safe_key(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_]+", value):
            raise ValueError(f"缓存键无效：{value}")
        return value

    def _write_json(self, path: Path, payload: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if path.exists() and path.read_text(encoding="utf-8") == serialized:
                return
            temporary_path = path.with_suffix(".tmp")
            temporary_path.write_text(serialized, encoding="utf-8")
            temporary_path.replace(path)
        except OSError as error:
            self._logger.warning("无法写入行情缓存 %s：%s", path, error)

    def _read_json(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError) as error:
            self._logger.warning("无法读取行情缓存 %s：%s", path, error)
            return None
