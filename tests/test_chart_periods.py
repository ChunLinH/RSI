from rsi_monitor.market_data.chart_periods import CHART_PERIOD_LABELS, CHART_PERIODS


def test_chart_period_switch_order_contains_all_requested_periods() -> None:
    assert CHART_PERIODS == (
        "intraday",
        "five_day",
        "1min",
        "5min",
        "15min",
        "30min",
        "60min",
        "120min",
        "day",
        "week",
        "month",
        "year",
    )
    assert set(CHART_PERIOD_LABELS) == set(CHART_PERIODS)
