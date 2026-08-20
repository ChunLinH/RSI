import json
from urllib.error import URLError

from rsi_monitor.market_data.alpha_vantage import AlphaVantageMarketDataProvider


INTRADAY_PAYLOAD = {
    "Meta Data": {"2. Symbol": "SPY"},
    "Time Series (1min)": {
        "2026-08-11 09:31:00": {"4. close": "101.50"},
        "2026-08-11 09:30:00": {"4. close": "100.25"},
    },
}


class FakeResponse:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def _provider(max_retries: int = 2) -> AlphaVantageMarketDataProvider:
    return AlphaVantageMarketDataProvider(
        symbol="SPY",
        interval="1min",
        api_key="test-key",
        base_url="https://example.test/query",
        entitlement="realtime",
        timezone="America/New_York",
        timeout_seconds=1,
        max_retries=max_retries,
        retry_delay_seconds=0,
    )


def test_fetches_latest_quote_name_and_price_history(monkeypatch) -> None:
    responses = iter([INTRADAY_PAYLOAD, {"Name": "SPDR S&P 500 ETF Trust"}])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, timeout))
        return FakeResponse(next(responses))

    monkeypatch.setattr("rsi_monitor.market_data.alpha_vantage.urlopen", fake_urlopen)
    provider = _provider()

    quote = provider.fetch_latest_quote()
    recent_quotes = provider.fetch_recent_quotes(10)

    assert quote.symbol == "SPY"
    assert quote.name == "SPDR S&P 500 ETF Trust"
    assert quote.price == 101.50
    assert [item.price for item in recent_quotes] == [100.25, 101.50]
    assert "function=TIME_SERIES_INTRADAY" in requests[0][0]
    assert "function=OVERVIEW" in requests[1][0]


def test_retries_transient_request_failure(monkeypatch) -> None:
    responses = iter([URLError("temporary outage"), INTRADAY_PAYLOAD, {"Name": "SPY"}])
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(request.full_url)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)

    monkeypatch.setattr("rsi_monitor.market_data.alpha_vantage.urlopen", fake_urlopen)
    monkeypatch.setattr("rsi_monitor.market_data.alpha_vantage.time.sleep", lambda _: None)

    quote = _provider().fetch_latest_quote()

    assert quote.price == 101.50
    assert len(attempts) == 3
