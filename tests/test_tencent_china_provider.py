from datetime import datetime
from urllib.parse import parse_qs, urlparse
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

from rsi_monitor.market_data.base import MarketQuote
from rsi_monitor.market_data.tencent_china import TencentChinaMarketDataProvider


class FakeResponse:
    status = 200

    def __init__(self, payload: str, encoding: str = "utf-8") -> None:
        self._payload = payload.encode(encoding)

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_loads_chinese_etf_quote(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse(
            'v_sz159952="51~创业板ETF~159952~2.166~2.158~0~0~20260811161442~";',
            "gbk",
        )

    monkeypatch.setattr("rsi_monitor.market_data.tencent_china.urlopen", fake_urlopen)
    provider = TencentChinaMarketDataProvider("159952", 3, 1, 0)

    quote = provider.fetch_latest_quote()

    assert captured["url"] == "https://qt.gtimg.cn/q=sz159952"
    assert captured["timeout"] == 3
    assert quote.name == "创业板ETF"
    assert quote.price == 2.166
    assert quote.change_percent == pytest.approx(0.3707136)
    assert quote.timestamp.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-11 16:14:42"


def test_quote_payload_exposes_auction_volume_and_amount(monkeypatch) -> None:
    fields = [""] * 52
    fields[0] = "51"
    fields[1] = "创业板ETF"
    fields[2] = "159952"
    fields[3] = "2.166"
    fields[4] = "2.158"
    fields[6] = "123"
    fields[7] = "70"
    fields[8] = "53"
    for index, value in enumerate(
        ("2.165", "11", "2.164", "22", "2.163", "33", "2.162", "44", "2.161", "55"),
        9,
    ):
        fields[index] = value
    for index, value in enumerate(
        ("2.167", "12", "2.168", "23", "2.169", "34", "2.170", "45", "2.171", "56"),
        19,
    ):
        fields[index] = value
    fields[30] = "20260813091501"
    fields[31] = "0.008"
    fields[33] = "2.170"
    fields[34] = "2.150"
    fields[35] = "2.166/123/26641.8"
    fields[38] = "1.25"
    fields[39] = "12.30"
    fields[43] = "0.93"
    fields[44] = "100.50"
    fields[45] = "120.75"
    fields[46] = "1.40"
    fields[47] = "2.374"
    fields[48] = "1.942"
    fields[49] = "1.18"
    fields[51] = "2.160"

    monkeypatch.setattr(
        "rsi_monitor.market_data.tencent_china.urlopen",
        lambda request, timeout: FakeResponse(
            f'v_sz159952="{"~".join(fields)}";', "gbk"
        ),
    )
    provider = TencentChinaMarketDataProvider("159952", 3, 1, 0)

    quote = provider.fetch_latest_quote()

    assert quote.cumulative_volume == 123
    assert quote.cumulative_amount == pytest.approx(26_641.8)
    assert quote.previous_close == 2.158
    assert quote.open_price is None
    assert quote.high_price == 2.170
    assert quote.low_price == 2.150
    assert quote.price_change == 0.008
    assert quote.turnover_rate == 1.25
    assert quote.amplitude == 0.93
    assert quote.average_price == 2.160
    assert quote.bid_levels[0] == (2.165, 11)
    assert quote.ask_levels[-1] == (2.171, 56)
    assert provider.get_opening_auction_bars(quote.timestamp.date())[0].close == 2.166


def test_collects_and_persists_opening_auction_snapshots(tmp_path) -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    provider = TencentChinaMarketDataProvider(
        "159952", 3, 1, 0, cache_directory=tmp_path
    )
    provider._record_auction_quote(
        MarketQuote(
            "159952",
            2.10,
            datetime(2026, 8, 13, 9, 15, 1, tzinfo=timezone),
            name="创业板ETF",
            is_trading=False,
            cumulative_volume=100,
            cumulative_amount=21_000,
        )
    )
    provider._record_auction_quote(
        MarketQuote(
            "159952",
            2.11,
            datetime(2026, 8, 13, 9, 15, 2, tzinfo=timezone),
            name="创业板ETF",
            is_trading=False,
            cumulative_volume=130,
            cumulative_amount=27_330,
        )
    )

    restored_provider = TencentChinaMarketDataProvider(
        "159952", 3, 1, 0, cache_directory=tmp_path
    )
    bars = restored_provider.get_opening_auction_bars(datetime(2026, 8, 13).date())

    assert [bar.close for bar in bars] == [2.10, 2.11]
    assert [bar.volume for bar in bars] == [100, 30]
    assert [bar.amount for bar in bars] == [21_000, 6_330]


def test_quote_outside_opening_auction_is_not_collected(tmp_path) -> None:
    provider = TencentChinaMarketDataProvider(
        "159952", 3, 1, 0, cache_directory=tmp_path
    )
    timestamp = datetime(2026, 8, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    provider._record_auction_quote(
        MarketQuote("159952", 2.10, timestamp, cumulative_volume=100)
    )

    assert provider.get_opening_auction_bars(timestamp.date()) == ()


def test_uses_shanghai_prefix_for_six_digit_stock(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        assert request.full_url == "https://qt.gtimg.cn/q=sh600000"
        return FakeResponse(
            'v_sh600000="51~浦发银行~600000~10.50~10.40~0~0~20260811150000~";',
            "gbk",
        )

    monkeypatch.setattr("rsi_monitor.market_data.tencent_china.urlopen", fake_urlopen)

    quote = TencentChinaMarketDataProvider("600000", 3, 1, 0).fetch_latest_quote()

    assert quote.name == "浦发银行"


def test_uses_shanghai_prefix_for_shanghai_composite_index(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        assert request.full_url == "https://qt.gtimg.cn/q=sh000001"
        return FakeResponse(
            'v_sh000001="51~上证指数~000001~3785.60~3760.20~0~0~20260811150000~";',
            "gbk",
        )

    monkeypatch.setattr("rsi_monitor.market_data.tencent_china.urlopen", fake_urlopen)

    quote = TencentChinaMarketDataProvider("000001", 3, 1, 0).fetch_latest_quote()

    assert quote.name == "上证指数"


def test_loads_recent_minute_closes_for_immediate_rsi(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return FakeResponse(
            '{"code":0,"msg":"","data":{"sz159952":{'
            '"qt":{"sz159952":["51","Test ETF"]},"prec":"2.10","m5":['
            '["202608120930","2.10","2.11","2.12","2.09","1"],'
            '["202608120935","2.11","2.12","2.13","2.10","2"],'
            '["202608120940","2.12","2.13","2.14","2.11","3"]'
            ']}}}'
        )

    monkeypatch.setattr("rsi_monitor.market_data.tencent_china.urlopen", fake_urlopen)
    provider = TencentChinaMarketDataProvider("159952", 3, 1, 0, interval="5min")

    quotes = provider.fetch_recent_quotes(2)

    parameters = parse_qs(urlparse(captured["url"]).query)
    assert parameters["param"] == ["sz159952,m5,,2"]
    assert [quote.price for quote in quotes] == [2.12, 2.13]
    assert quotes[-1].timestamp.strftime("%Y-%m-%d %H:%M") == "2026-08-12 09:40"


def test_falls_back_to_cached_quote_without_marking_it_live(monkeypatch, tmp_path) -> None:
    responses = iter(
        [
            FakeResponse(
                'v_sz159952="51~创业板ETF~159952~2.166~2.158~0~0~20260811150000~";',
                "gbk",
            ),
            URLError("offline"),
        ]
    )

    def fake_urlopen(request, timeout):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("rsi_monitor.market_data.tencent_china.urlopen", fake_urlopen)
    provider = TencentChinaMarketDataProvider(
        "159952", 3, 1, 0, cache_directory=tmp_path
    )

    first = provider.fetch_latest_quote()
    cached = provider.fetch_latest_quote()

    assert first.is_stale is False
    assert cached.price == 2.166
    assert cached.is_stale is True
    assert cached.is_trading is False


def test_parses_intraday_volume_and_filters_after_market_entries(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return FakeResponse(
            '{"code":0,"msg":"","data":{"sz159952":{'
            '"qt":{"sz159952":["51","创业板ETF"]},"prec":"2.10",'
            '"data":{"date":"20260812","data":['
            '"0930 2.100 100 21000.00",'
            '"0931 2.110 130 27330.00",'
            '"1501 2.120 140 29450.00"'
            ']}}}}'
        )

    monkeypatch.setattr("rsi_monitor.market_data.tencent_china.urlopen", fake_urlopen)
    provider = TencentChinaMarketDataProvider("159952", 3, 1, 0)

    series = provider.fetch_chart_series("intraday", 240)

    assert series.name == "创业板ETF"
    assert [bar.close for bar in series.bars] == [2.1, 2.11]
    assert [bar.volume for bar in series.bars] == [100, 30]
    assert [bar.amount for bar in series.bars] == [21000, 6330]
