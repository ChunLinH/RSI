from urllib.parse import parse_qs, urlparse

from rsi_monitor.market_data.security_search import (
    SecuritySearchResult,
    TencentSecuritySearcher,
)


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


def test_parses_chinese_name_search_results_and_filters_non_a_share_items() -> None:
    payload = (
        'v_hint="sz~159952~\\u521b\\u4e1a\\u677fETF\\u5e7f\\u53d1~'
        'cybetfgf~ETF^sh~600000~\\u6d66\\u53d1\\u94f6\\u884c~pfyh~GP-A^'
        'hk~00700~\\u817e\\u8baf~tx~GP-HK^bad-result"'
    )

    assert TencentSecuritySearcher._parse_response(payload) == [
        SecuritySearchResult("159952", "创业板ETF广发", "sz"),
        SecuritySearchResult("600000", "浦发银行", "sh"),
    ]


def test_search_requests_all_markets_and_returns_results(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse('v_hint="sh~600000~浦发银行~pfyh~GP-A"')

    monkeypatch.setattr(
        "rsi_monitor.market_data.security_search.urlopen", fake_urlopen
    )

    results = TencentSecuritySearcher(3, 1, 0).search("浦发银行")

    assert parse_qs(urlparse(captured["url"]).query) == {
        "q": ["浦发银行"],
        "t": ["all"],
    }
    assert captured["timeout"] == 3
    assert results == [SecuritySearchResult("600000", "浦发银行", "sh")]
