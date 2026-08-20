from datetime import datetime, timezone
import json
from urllib.parse import parse_qs
from urllib.error import URLError

from rsi_monitor.market_data.base import MarketQuote
from rsi_monitor.notifications.base import NotificationMessage
from rsi_monitor.notifications.formatters import create_signal_notification
from rsi_monitor.notifications.serverchan import ServerChanNotifier
from rsi_monitor.notifications.wxpusher import WxPusherNotifier
from rsi_monitor.signals.evaluator import SignalCondition


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


def test_formats_buy_notification_as_requested() -> None:
    quote = MarketQuote(
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        price=501.23,
        timestamp=datetime(2026, 8, 11, 9, 30),
    )

    message = create_signal_notification(quote, 29.8, "5min", SignalCondition.BUY, rsi_period=6)

    assert message.title == "🟢 买入｜SPDR S&P 500 ETF Trust（SPY）"
    assert message.content == (
        "🟢 买入｜SPDR S&P 500 ETF Trust（SPY）\n\n"
        "标的：SPY\n"
        "名称：SPDR S&P 500 ETF Trust\n"
        "当前价格：501.230\n"
        "RSI(6)：29.80\n"
        "周期：5min\n"
        "信号：RSI跌破30\n"
        "时间：2026-08-11 09:30:00 "
    )


def test_formats_sell_notification_as_requested() -> None:
    quote = MarketQuote(
        symbol="QQQ",
        name="Invesco QQQ Trust",
        price=523.45,
        timestamp=datetime(2026, 8, 11, 9, 45, tzinfo=timezone.utc),
    )

    message = create_signal_notification(quote, 70.2, "15min", SignalCondition.SELL, rsi_period=6)

    assert message.title == "🔴 卖出｜Invesco QQQ Trust（QQQ）"
    assert message.content == (
        "🔴 卖出｜Invesco QQQ Trust（QQQ）\n\n"
        "标的：QQQ\n"
        "名称：Invesco QQQ Trust\n"
        "当前价格：523.450\n"
        "RSI(6)：70.20\n"
        "周期：15min\n"
        "信号：RSI突破70\n"
        "时间：2026-08-11 09:45:00 UTC"
    )


def test_serverchan_posts_title_and_desp_and_logs_success(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = parse_qs(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"code": 0, "message": "success"})

    monkeypatch.setattr("rsi_monitor.notifications.serverchan.urlopen", fake_urlopen)
    notifier = ServerChanNotifier("test-key", 3, enabled=True)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is True
    assert result.status == "已发送"
    assert captured["url"] == "https://sctapi.ftqq.com/test-key.send"
    assert captured["payload"] == {"title": ["测试"], "desp": ["测试内容"]}
    assert captured["timeout"] == 3
    assert "Server酱通知已发送" in caplog.text


def test_serverchan_failure_is_logged_and_returned_without_raising(monkeypatch, caplog) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("连接失败")

    monkeypatch.setattr("rsi_monitor.notifications.serverchan.urlopen", fake_urlopen)
    notifier = ServerChanNotifier("test-key", 3, enabled=True)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is False
    assert result.status.startswith("发送失败")
    assert "Server酱通知发送失败" in caplog.text


def test_disabled_serverchan_notifier_does_not_send_request(monkeypatch) -> None:
    def fail_if_called(request, timeout):
        raise AssertionError("关闭通知时不应调用 Server酱")

    monkeypatch.setattr("rsi_monitor.notifications.serverchan.urlopen", fail_if_called)
    notifier = ServerChanNotifier("test-key", 3, enabled=False)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is False
    assert result.status == "通知已关闭"


def test_wxpusher_posts_json_and_logs_success(monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"code": 1000, "msg": "处理成功", "success": True})

    monkeypatch.setattr("rsi_monitor.notifications.wxpusher.urlopen", fake_urlopen)
    notifier = WxPusherNotifier("SPT_test", 3, enabled=True)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is True
    assert result.status == "已发送"
    assert captured["url"] == (
        "https://wxpusher.zjiecode.com/api/send/message/simple-push"
    )
    assert captured["payload"] == {
        "content": "测试内容",
        "summary": "测试",
        "contentType": 1,
        "spt": "SPT_test",
    }
    assert captured["timeout"] == 3
    assert "WxPusher 通知已发送" in caplog.text


def test_wxpusher_failure_is_logged_and_returned_without_raising(monkeypatch, caplog) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("连接失败")

    monkeypatch.setattr("rsi_monitor.notifications.wxpusher.urlopen", fake_urlopen)
    notifier = WxPusherNotifier("SPT_test", 3, enabled=True)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is False
    assert result.status.startswith("发送失败")
    assert "WxPusher 通知发送失败" in caplog.text


def test_disabled_wxpusher_notifier_does_not_send_request(monkeypatch) -> None:
    def fail_if_called(request, timeout):
        raise AssertionError("关闭通知时不应调用 WxPusher")

    monkeypatch.setattr("rsi_monitor.notifications.wxpusher.urlopen", fail_if_called)
    notifier = WxPusherNotifier("SPT_test", 3, enabled=False)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is False
    assert result.status == "通知已关闭"


def test_wxpusher_placeholder_is_reported_without_request(monkeypatch) -> None:
    def fail_if_called(request, timeout):
        raise AssertionError("未配置 SPT 时不应发起请求")

    monkeypatch.setattr("rsi_monitor.notifications.wxpusher.urlopen", fail_if_called)
    notifier = WxPusherNotifier("你的SPT", 3, enabled=True)

    result = notifier.send(NotificationMessage("测试", "测试内容"))

    assert result.sent is False
    assert result.status == "发送失败：未配置 WxPusher SPT"
