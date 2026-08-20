from rsi_monitor.market_data.simulated import SimulatedMarketDataProvider


def test_simulated_quotes_are_repeatable_with_same_seed() -> None:
    first = SimulatedMarketDataProvider("ETF", 100, 0.01, 2, 8)
    second = SimulatedMarketDataProvider("ETF", 100, 0.01, 2, 8)

    first_prices = [first.fetch_latest_quote().price for _ in range(5)]
    second_prices = [second.fetch_latest_quote().price for _ in range(5)]

    assert first_prices == second_prices
    assert all(price > 0 for price in first_prices)
