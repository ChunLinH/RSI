from dataclasses import replace
from datetime import datetime, timedelta

from rsi_monitor.market_data.base import ChartSeries, MarketBar, MarketQuote


def apply_live_quote(
    series: ChartSeries,
    quote: MarketQuote,
    previous_quote: MarketQuote | None = None,
) -> ChartSeries:
    """用最新成交价更新图表最右侧的未完成 K 线。"""
    if (
        quote.symbol != series.symbol
        or quote.is_stale
        or not quote.is_trading
        or not series.bars
    ):
        return series

    bars = list(series.bars)
    latest = bars[-1]
    same_period = _period_key(series.period, latest.timestamp) == _period_key(
        series.period, quote.timestamp
    )
    volume_delta = _cumulative_delta(
        quote.cumulative_volume,
        previous_quote.cumulative_volume if previous_quote is not None else None,
        quote,
        previous_quote,
    )
    amount_delta = _cumulative_delta(
        quote.cumulative_amount,
        previous_quote.cumulative_amount if previous_quote is not None else None,
        quote,
        previous_quote,
    )

    if same_period:
        bars[-1] = _update_bar(
            latest,
            quote,
            series.period,
            volume_delta,
            amount_delta,
        )
    elif quote.timestamp > latest.timestamp:
        bars.append(
            _new_bar(
                quote,
                series.period,
                volume_delta,
                amount_delta,
            )
        )
    else:
        return series
    return replace(series, bars=tuple(bars), is_stale=False)


def _update_bar(
    bar: MarketBar,
    quote: MarketQuote,
    period: str,
    volume_delta: float,
    amount_delta: float,
) -> MarketBar:
    high = quote.price
    low = quote.price
    if period in {"day", "week", "month", "year"}:
        high = quote.high_price or high
        low = quote.low_price or low
    return MarketBar(
        timestamp=bar.timestamp,
        open=bar.open,
        high=max(bar.high, high),
        low=min(bar.low, low),
        close=quote.price,
        volume=bar.volume + volume_delta,
        amount=(bar.amount + amount_delta) if bar.amount is not None else None,
    )


def _new_bar(
    quote: MarketQuote,
    period: str,
    volume_delta: float,
    amount_delta: float,
) -> MarketBar:
    long_period = period in {"day", "week", "month", "year"}
    open_price = (quote.open_price or quote.price) if long_period else quote.price
    high = (quote.high_price or quote.price) if long_period else quote.price
    low = (quote.low_price or quote.price) if long_period else quote.price
    return MarketBar(
        timestamp=_bar_timestamp(period, quote.timestamp),
        open=open_price,
        high=high,
        low=low,
        close=quote.price,
        volume=volume_delta,
        amount=amount_delta if quote.cumulative_amount is not None else None,
    )


def _cumulative_delta(
    current: float | None,
    previous: float | None,
    quote: MarketQuote,
    previous_quote: MarketQuote | None,
) -> float:
    if (
        current is None
        or previous is None
        or previous_quote is None
        or quote.timestamp.date() != previous_quote.timestamp.date()
    ):
        return 0.0
    return max(current - previous, 0.0)


def _period_key(period: str, timestamp: datetime) -> object:
    if period in {"intraday", "five_day"}:
        return timestamp.replace(second=0, microsecond=0)
    if period.endswith("min"):
        return _minute_bar_end(period, timestamp)
    if period == "day":
        return timestamp.date()
    if period == "week":
        calendar = timestamp.isocalendar()
        return calendar.year, calendar.week
    if period == "month":
        return timestamp.year, timestamp.month
    if period == "year":
        return timestamp.year
    return timestamp


def _bar_timestamp(period: str, timestamp: datetime) -> datetime:
    if period in {"intraday", "five_day"}:
        return timestamp.replace(second=0, microsecond=0)
    if period.endswith("min"):
        return _minute_bar_end(period, timestamp)
    return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)


def _minute_bar_end(period: str, timestamp: datetime) -> datetime:
    minutes = int(period.removesuffix("min"))
    value = timestamp.replace(second=0, microsecond=0)
    if minutes == 60:
        endings = ((10, 30), (11, 30), (14, 0), (15, 0))
        return _session_bar_end(value, endings)
    if minutes == 120:
        return _session_bar_end(value, ((11, 30), (15, 0)))
    remainder = value.minute % minutes
    return value if remainder == 0 else value + timedelta(minutes=minutes - remainder)


def _session_bar_end(
    timestamp: datetime, endings: tuple[tuple[int, int], ...]
) -> datetime:
    current_minutes = timestamp.hour * 60 + timestamp.minute
    for hour, minute in endings:
        if current_minutes <= hour * 60 + minute:
            return timestamp.replace(hour=hour, minute=minute)
    hour, minute = endings[-1]
    return timestamp.replace(hour=hour, minute=minute)
