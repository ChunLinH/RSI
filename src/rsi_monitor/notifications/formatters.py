from rsi_monitor.market_data.base import MarketQuote
from rsi_monitor.notifications.base import NotificationMessage
from rsi_monitor.signals.evaluator import SignalCondition


def create_signal_notification(
    quote: MarketQuote,
    rsi: float,
    interval: str,
    signal: SignalCondition,
    rsi_period: int = 14,
) -> NotificationMessage:
    instrument = _instrument_label(quote)
    if signal is SignalCondition.BUY:
        title = f"🟢 买入｜{instrument}"
        signal_text = "RSI跌破30"
    elif signal is SignalCondition.SELL:
        title = f"🔴 卖出｜{instrument}"
        signal_text = "RSI突破70"
    else:
        raise ValueError("仅能为买入或卖出信号创建通知")

    content = "\n".join(
        (
            title,
            "",
            f"标的：{quote.symbol}",
            f"名称：{quote.name or '名称暂不可用'}",
            f"当前价格：{quote.price:.3f}",
            f"RSI({rsi_period})：{rsi:.2f}",
            f"周期：{interval}",
            f"信号：{signal_text}",
            f"时间：{quote.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        )
    )
    return NotificationMessage(title=title, content=content)


def _instrument_label(quote: MarketQuote) -> str:
    name = quote.name.strip()
    return f"{name}（{quote.symbol}）" if name else quote.symbol


def create_test_notification(symbol: str, interval: str) -> NotificationMessage:
    title = "RSI 盯盘助手测试通知"
    return NotificationMessage(
        title=title,
        content=f"{title}\n\n标的：{symbol}\n周期：{interval}\n这是一条 RSI 盯盘助手测试通知。",
    )
