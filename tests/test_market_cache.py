from datetime import datetime

from rsi_monitor.market_data.base import ChartSeries, MarketBar, MarketQuote
from rsi_monitor.market_data.cache import MarketDataCache


def test_market_cache_round_trips_quote_and_chart_series(tmp_path) -> None:
    cache = MarketDataCache(tmp_path)
    timestamp = datetime.now().astimezone()
    quote = MarketQuote(
        "159952",
        2.199,
        timestamp,
        "创业板ETF",
        1.52,
        cumulative_volume=1_000,
        cumulative_amount=219_900,
        previous_close=2.166,
        bid_levels=((2.198, 100),),
        ask_levels=((2.200, 200),),
    )
    auction_bar = MarketBar(timestamp, 2.18, 2.18, 2.18, 2.18, 100, 21_800)
    series = ChartSeries(
        "159952",
        "创业板ETF",
        "1min",
        (MarketBar(timestamp, 2.19, 2.21, 2.18, 2.199, 1000, 219900),),
        2.166,
        auction_bars=(auction_bar,),
    )

    cache.save_quote(quote)
    cache.save_series(series)

    cached_quote = cache.load_quote("159952")
    cached_series = cache.load_series("159952", "1min")
    assert cached_quote is not None
    assert cached_quote.price == 2.199
    assert cached_quote.is_stale is True
    assert cached_quote.is_trading is False
    assert cached_quote.cumulative_volume == 1_000
    assert cached_quote.cumulative_amount == 219_900
    assert cached_quote.previous_close == 2.166
    assert cached_quote.bid_levels == ((2.198, 100),)
    assert cached_quote.ask_levels == ((2.200, 200),)
    assert cached_series is not None
    assert cached_series.bars == series.bars
    assert cached_series.auction_bars == (auction_bar,)
    assert cached_series.is_stale is True
