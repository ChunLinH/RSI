from datetime import datetime
from zoneinfo import ZoneInfo

from rsi_monitor.market_data.base import ChartSeries, MarketBar, MarketQuote
from rsi_monitor.market_data.live_series import apply_live_quote


TIMEZONE = ZoneInfo("Asia/Shanghai")


def quote(
    timestamp: datetime,
    price: float,
    volume: float = 1_100,
    amount: float = 110_000,
    **values,
) -> MarketQuote:
    return MarketQuote(
        "159952",
        price,
        timestamp.replace(tzinfo=TIMEZONE),
        cumulative_volume=volume,
        cumulative_amount=amount,
        **values,
    )


def test_live_quote_updates_current_five_minute_bar_and_volume_delta() -> None:
    timestamp = datetime(2026, 8, 13, 11, 15, tzinfo=TIMEZONE)
    series = ChartSeries(
        "159952",
        "创业板ETF广发",
        "5min",
        (MarketBar(timestamp, 2.230, 2.236, 2.228, 2.228, 500, 50_000),),
    )
    previous = quote(datetime(2026, 8, 13, 11, 11), 2.229, 1_000, 100_000)
    current = quote(datetime(2026, 8, 13, 11, 12), 2.240, 1_100, 110_000)

    updated = apply_live_quote(series, current, previous)

    assert len(updated.bars) == 1
    assert updated.bars[-1] == MarketBar(
        timestamp, 2.230, 2.240, 2.228, 2.240, 600, 60_000
    )


def test_live_quote_starts_next_minute_bar_at_provider_boundary() -> None:
    series = ChartSeries(
        "159952",
        "创业板ETF广发",
        "5min",
        (
            MarketBar(
                datetime(2026, 8, 13, 11, 15, tzinfo=TIMEZONE),
                2.228,
                2.240,
                2.225,
                2.230,
                500,
            ),
        ),
    )
    previous = quote(datetime(2026, 8, 13, 11, 15), 2.230, 1_100)
    current = quote(datetime(2026, 8, 13, 11, 16), 2.235, 1_180)

    updated = apply_live_quote(series, current, previous)

    assert len(updated.bars) == 2
    assert updated.bars[-1].timestamp == datetime(
        2026, 8, 13, 11, 20, tzinfo=TIMEZONE
    )
    assert updated.bars[-1].open == 2.235
    assert updated.bars[-1].volume == 80


def test_sixty_and_one_hundred_twenty_minute_bars_follow_trading_sessions() -> None:
    sixty = ChartSeries(
        "159952",
        "创业板ETF广发",
        "60min",
        (
            MarketBar(
                datetime(2026, 8, 13, 10, 30, tzinfo=TIMEZONE),
                2.2,
                2.3,
                2.1,
                2.2,
                100,
            ),
        ),
    )
    one_twenty = ChartSeries(
        "159952",
        "创业板ETF广发",
        "120min",
        sixty.bars,
    )
    current = quote(datetime(2026, 8, 13, 11, 10), 2.25)

    updated_sixty = apply_live_quote(sixty, current)
    updated_one_twenty = apply_live_quote(one_twenty, current)

    assert updated_sixty.bars[-1].timestamp == datetime(
        2026, 8, 13, 11, 30, tzinfo=TIMEZONE
    )
    assert len(updated_sixty.bars) == 2
    assert len(updated_one_twenty.bars) == 1
    assert updated_one_twenty.bars[-1].close == 2.25


def test_live_quote_updates_current_daily_ohlcv_from_daily_fields() -> None:
    timestamp = datetime(2026, 8, 13, tzinfo=TIMEZONE)
    series = ChartSeries(
        "159952",
        "创业板ETF广发",
        "day",
        (MarketBar(timestamp, 2.222, 2.240, 2.220, 2.230, 1_000),),
    )
    current = quote(
        datetime(2026, 8, 13, 11, 12),
        2.245,
        volume=2_000,
        open_price=2.222,
        high_price=2.246,
        low_price=2.219,
    )

    updated = apply_live_quote(series, current)

    assert updated.bars[-1].open == 2.222
    assert updated.bars[-1].high == 2.246
    assert updated.bars[-1].low == 2.219
    assert updated.bars[-1].close == 2.245


def test_stale_or_closed_quote_never_changes_chart() -> None:
    timestamp = datetime(2026, 8, 13, 11, 12, tzinfo=TIMEZONE)
    series = ChartSeries(
        "159952",
        "创业板ETF广发",
        "1min",
        (MarketBar(timestamp, 2.2, 2.2, 2.2, 2.2, 100),),
    )

    stale = apply_live_quote(series, quote(timestamp, 2.3, is_stale=True))
    closed = apply_live_quote(series, quote(timestamp, 2.3, is_trading=False))

    assert stale is series
    assert closed is series
