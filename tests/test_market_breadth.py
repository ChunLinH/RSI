from datetime import datetime
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from rsi_monitor.market_data.base import MarketDataError
from rsi_monitor.market_data.breadth import EastmoneyMarketBreadthProvider


class FakeResponse:
    status = 200

    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_fetches_all_a_breadth(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured.setdefault("urls", []).append(request.full_url)
        captured["timeout"] = timeout
        return FakeResponse(
            '{"rc":0,"data":{"diff":[{"f3":-2.20,"f6":500000000000,'
            '"f12":"800000",'
            '"f14":"东方财富全A","f104":500,"f105":4650,'
            '"f106":60,"f124":1787105703},{"f3":-2.23,'
            '"f6":510000000000,"f12":"800004",'
            '"f14":"东方财富全A(沪深京)","f104":534,"f105":4953,'
            '"f106":60,"f124":1787105703}]}}'
        )

    def fake_chart_series(provider, period, limit):
        assert period == "five_day"
        assert limit == 2_000
        return SimpleNamespace(
            bars=(
                SimpleNamespace(
                    timestamp=datetime(2026, 8, 18, 9, 30),
                    amount=50_000_000_000,
                ),
                SimpleNamespace(
                    timestamp=datetime(2026, 8, 18, 10, 15),
                    amount=100_000_000_000,
                ),
                SimpleNamespace(
                    timestamp=datetime(2026, 8, 18, 10, 16),
                    amount=900_000_000_000,
                ),
            )
        )

    monkeypatch.setattr("rsi_monitor.market_data.breadth.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "rsi_monitor.market_data.breadth.TencentChinaMarketDataProvider.fetch_chart_series",
        fake_chart_series,
    )
    provider = EastmoneyMarketBreadthProvider(3, 1, 0)

    breadth = provider.fetch_all_a()

    assert "secids=47.800000" in captured["urls"][0]
    assert "47.800004" in captured["urls"][0]
    assert captured["timeout"] == 3
    assert breadth.name == "沪深京全A"
    assert breadth.up_count == 534
    assert breadth.down_count == 4953
    assert breadth.flat_count == 60
    assert breadth.change_percent == -2.23
    assert breadth.timestamp is not None
    assert breadth.turnover_amount == 500_000_000_000
    assert breadth.previous_same_time_amount == 300_000_000_000


def test_rejects_invalid_all_a_breadth_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "rsi_monitor.market_data.breadth.urlopen",
        lambda request, timeout: FakeResponse('{"rc":0,"data":{"diff":[]}}'),
    )
    provider = EastmoneyMarketBreadthProvider(3, 1, 0)

    with pytest.raises(MarketDataError, match="格式无效"):
        provider.fetch_all_a()


def test_keeps_current_amount_when_previous_comparison_is_unavailable(
    monkeypatch,
) -> None:
    def fake_urlopen(request, timeout):
        return FakeResponse(
            '{"rc":0,"data":{"diff":[{"f3":1.25,"f6":500000000000,'
            '"f12":"800000","f14":"东方财富全A","f104":3500,'
            '"f105":1500,"f106":100,"f124":1787105703},{"f3":1.20,'
            '"f6":510000000000,"f12":"800004",'
            '"f14":"东方财富全A(沪深京)","f104":3530,'
            '"f105":1800,"f106":100,"f124":1787105703}]}}'
        )

    def unavailable_chart_series(provider, period, limit):
        raise MarketDataError("history unavailable")

    monkeypatch.setattr("rsi_monitor.market_data.breadth.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "rsi_monitor.market_data.breadth.TencentChinaMarketDataProvider.fetch_chart_series",
        unavailable_chart_series,
    )
    provider = EastmoneyMarketBreadthProvider(3, 1, 0)

    breadth = provider.fetch_all_a()

    assert breadth.turnover_amount == 500_000_000_000
    assert breadth.previous_same_time_amount is None


def test_uses_fallback_endpoint_when_primary_is_unavailable(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if "push2.eastmoney.com" in request.full_url:
            raise URLError("primary unavailable")
        return FakeResponse(
            '{"rc":0,"data":{"diff":[{"f3":-0.5,"f6":400000000000,'
            '"f12":"800000","f14":"东方财富全A","f104":2000,'
            '"f105":3000,"f106":100,"f124":0},{"f3":-0.6,'
            '"f6":410000000000,"f12":"800004",'
            '"f14":"东方财富全A(沪深京)","f104":2030,'
            '"f105":3300,"f106":100,"f124":0}]}}'
        )

    monkeypatch.setattr("rsi_monitor.market_data.breadth.urlopen", fake_urlopen)
    provider = EastmoneyMarketBreadthProvider(3, 1, 0)

    breadth = provider.fetch_all_a()

    assert "push2.eastmoney.com" in requested_urls[0]
    assert "push2delay.eastmoney.com" in requested_urls[1]
    assert breadth.down_count == 3300
    assert breadth.turnover_amount == 400_000_000_000


def test_uses_test_endpoint_when_both_normal_endpoints_are_unavailable(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if "push2test.eastmoney.com" not in request.full_url:
            raise URLError("endpoint unavailable")
        return FakeResponse(
            '{"rc":0,"data":{"diff":[{"f3":0.1,"f6":500000000000,'
            '"f12":"800000","f104":2400,"f105":2600,"f106":100,'
            '"f124":1787105703},{"f3":0.2,"f6":510000000000,'
            '"f12":"800004","f104":2500,"f105":2700,"f106":120,'
            '"f124":1787105703}]}}'
        )

    monkeypatch.setattr("rsi_monitor.market_data.breadth.urlopen", fake_urlopen)
    provider = EastmoneyMarketBreadthProvider(3, 1, 0)

    breadth = provider.fetch_all_a()

    assert "push2.eastmoney.com" in requested_urls[0]
    assert "push2delay.eastmoney.com" in requested_urls[1]
    assert "push2test.eastmoney.com" in requested_urls[2]
    assert breadth.up_count == 2500
    assert breadth.down_count == 2700


def test_reuses_last_successful_breadth_when_all_endpoints_fail(monkeypatch, tmp_path) -> None:
    responses = iter(
        [
            FakeResponse(
                '{"rc":0,"data":{"diff":[{"f3":1.0,"f6":500,'
                '"f12":"800000","f104":10,"f105":20,"f106":30,'
                '"f124":1787105703},{"f3":1.0,"f6":600,'
                '"f12":"800004","f104":11,"f105":21,"f106":31,'
                '"f124":1787105703}]}}'
            )
        ]
    )

    def fake_urlopen(request, timeout):
        try:
            return next(responses)
        except StopIteration as error:
            raise URLError("all endpoints unavailable") from error

    monkeypatch.setattr("rsi_monitor.market_data.breadth.urlopen", fake_urlopen)
    provider = EastmoneyMarketBreadthProvider(3, 1, 0, cache_directory=tmp_path)

    first = provider.fetch_all_a()
    second = provider.fetch_all_a()

    assert second == first
    assert (tmp_path / "market_breadth.json").exists()
