from datetime import datetime
from threading import Event

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from rsi_monitor.indicators.rsi import RsiCalculator
from rsi_monitor.market_data.base import (
    ChartMarketDataProvider,
    ChartSeries,
    MarketBar,
    MarketDataProvider,
    MarketQuote,
)
from rsi_monitor.market_data.breadth import MarketBreadth
from rsi_monitor.market_data.security_search import SecuritySearchResult
from rsi_monitor.monitoring.engine import MonitorEngine, MonitorSnapshot
from rsi_monitor.monitoring.session_store import MonitorDefinition
from rsi_monitor.notifications.console import ConsoleNotificationService
from rsi_monitor.notifications.base import NotificationResult
from rsi_monitor.signals.evaluator import SignalCondition, SignalDetector
from rsi_monitor.ui.main_window import ChartUpdateWorker, MainWindow, MonitorSession


class OneQuoteProvider(ChartMarketDataProvider):
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    def fetch_latest_quote(self) -> MarketQuote:
        return MarketQuote(
            self._symbol,
            100.0,
            datetime.now().astimezone(),
            name="测试标的",
            change_percent=1.75,
        )

    def fetch_recent_quotes(self, limit: int) -> list[MarketQuote]:
        return [self.fetch_latest_quote()]

    def fetch_chart_series(self, period: str, limit: int) -> ChartSeries:
        timestamp = datetime.now().astimezone()
        return ChartSeries(
            self._symbol,
            "测试标的",
            period,
            (MarketBar(timestamp, 99, 101, 98, 100, 1_000),),
            98.28,
        )


def test_window_starts_and_stops_monitoring(qtbot) -> None:
    saved_monitors: list[list[MonitorDefinition]] = []

    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        assert symbol in {"159952", "600000"}
        assert period == "1min"
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min", "5min", "15min"),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
        session_saver=saved_monitors.append,
    )
    qtbot.addWidget(window)

    window.add_monitor()
    window._symbol_input.setText("600000")
    window.add_monitor()
    window.start_monitoring()

    qtbot.waitUntil(lambda: window._table.item(0, 3).text() == "100.000")

    assert window._timer.isActive()
    assert window._timer.interval() == 1_000
    assert window._monitor_status.text() == "监控中（2 个标的）"
    assert window._table.rowCount() == 2
    assert window._table.item(0, 0).text() == "159952"
    assert window._table.item(0, 3).text() == "100.000"
    assert window._table.item(0, window._connection_column).text() == "已连接"
    assert [window._table.horizontalHeaderItem(column).text() for column in (5,)] == ["RSI(6)"]
    assert window._sessions[0].change_percent == 1.75
    assert window._sessions[0].price == 100.0

    window._table.selectRow(0)
    window._buy_threshold.setValue(25)
    window._sell_threshold.setValue(75)
    window.update_selected_thresholds()

    assert window._table.item(0, window._buy_threshold_column).text() == "25.0"
    assert window._table.item(0, window._sell_threshold_column).text() == "75.0"
    assert saved_monitors[-1][0] == MonitorDefinition("159952", "1min", 25, 75)

    window._table.item(0, window._buy_threshold_column).setText("20")

    assert window._sessions[0].buy_threshold == 20
    assert window._table.item(0, window._buy_threshold_column).text() == "20"
    assert saved_monitors[-1][0] == MonitorDefinition("159952", "1min", 20, 75)

    window._table.item(0, window._buy_threshold_column).setText("80")

    assert window._sessions[0].buy_threshold == 20
    assert window._table.item(0, window._buy_threshold_column).text() == "20.0"

    window.test_notification()

    assert window._notification_status.text() == "测试通知成功：已发送"

    window.stop_monitoring()

    assert not window._timer.isActive()
    window.stop_monitoring()
    assert window._monitor_status.text() == "已停止"


def test_window_restores_saved_monitor_list(qtbot) -> None:
    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min", "5min", "15min"),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
        saved_monitors=[MonitorDefinition("159952", "5min", 25, 75)],
    )
    qtbot.addWidget(window)

    assert window._table.rowCount() == 1
    assert window._table.item(0, 0).text() == "159952"
    assert window._table.item(0, 2).text() == "5min"
    assert window._table.item(0, window._buy_threshold_column).text() == "25.0"
    assert window._table.item(0, window._sell_threshold_column).text() == "75.0"
    assert window._timer.isActive()
    window.stop_monitoring()


def test_window_resolves_chinese_security_name_before_adding_monitor(qtbot) -> None:
    searched_queries: list[str] = []

    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        assert symbol == "600000"
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    def search_security(query: str) -> list[SecuritySearchResult]:
        searched_queries.append(query)
        return [SecuritySearchResult("600000", "浦发银行", "sh")]

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
        security_searcher=search_security,
        system_tray_available=lambda: False,
    )
    qtbot.addWidget(window)

    window._symbol_input.setText("浦发银行")
    window.add_monitor()

    assert searched_queries == ["浦发银行"]
    assert window._symbol_input.text() == "600000"
    assert window._sessions[0].symbol == "600000"
    assert window._table.item(0, 0).text() == "600000"


def test_window_close_hides_to_system_tray_and_keeps_monitoring(qtbot) -> None:
    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
        system_tray_available=lambda: True,
    )
    qtbot.addWidget(window)
    window.show()
    window.add_monitor()
    window.start_monitoring()

    window.close()

    assert window.has_system_tray
    assert window.isHidden()
    assert window._timer.isActive()
    qtbot.wait(250)
    assert not window._mini_overlay.isVisible()

    window._allow_quit = True
    window.close()


def test_window_shows_mini_overlay_when_minimized(qtbot) -> None:
    saved_preferences = []

    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
        system_tray_available=lambda: True,
        preference_saver=saved_preferences.append,
    )
    qtbot.addWidget(window)
    window.show()
    window.add_monitor()
    window.start_monitoring()
    qtbot.waitUntil(lambda: window._sessions[0].change_percent == 1.75)

    window.showMinimized()
    qtbot.wait(200)

    assert window.isHidden()
    assert window._mini_overlay.isVisible()
    assert [label.text() for label in window._mini_overlay.findChildren(QLabel)] == [
        "测试标的",
        "--",
        "100.000",
        "+1.75%",
    ]

    first_row_height = window._mini_overlay._row_labels[0][0].height()
    QTest.mouseClick(
        window._mini_overlay,
        Qt.MouseButton.LeftButton,
        pos=QPoint(20, 6 + first_row_height // 2),
    )
    qtbot.waitUntil(
        lambda: window._intraday_chart._data is not None
        and window._intraday_chart._data.series is not None
    )

    assert window._intraday_chart.isVisible()
    assert window._intraday_chart._data is not None
    assert window._intraday_chart._data.symbol == "159952"
    assert window._intraday_chart._data.series is not None
    assert [bar.close for bar in window._intraday_chart._data.series.bars] == [100]
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window._intraday_chart)
    assert window._intraday_chart.hasFocus()

    # 悬浮条可能在打开图表后重新取得焦点，快捷键仍应切换图表周期。
    QTest.keyClick(window._mini_overlay, Qt.Key.Key_PageDown)
    qtbot.waitUntil(
        lambda: window._intraday_chart._data is not None
        and window._intraday_chart._data.period == "five_day"
        and window._intraday_chart._data.series is not None
    )

    assert window._intraday_chart._data.period == "five_day"

    window._intraday_chart._period_selector.setCurrentIndex(
        window._intraday_chart._period_selector.findData("15min")
    )
    qtbot.waitUntil(
        lambda: window._intraday_chart._data is not None
        and window._intraday_chart._data.period == "15min"
        and window._intraday_chart._data.series is not None
    )

    assert window._intraday_chart._data.period == "15min"

    window._auto_hide_chart_checkbox.setChecked(False)
    window._show_call_auction_checkbox.setChecked(False)
    window._intraday_chart.set_quote_panel_collapsed(True)
    window._intraday_chart._indicator_selector.setCurrentIndex(2)

    assert saved_preferences[-1].chart_auto_hide is False
    assert saved_preferences[-1].chart_indicator == "kdj"
    assert saved_preferences[-1].show_call_auction is False
    assert saved_preferences[-1].quote_panel_collapsed is True
    assert window._intraday_chart._data.show_call_auction is False

    window._toggle_intraday_chart("159952")

    assert not window._intraday_chart.isVisible()

    window._restore_from_mini_overlay()

    assert not window._mini_overlay.isVisible()
    assert window.isVisible()

    dragged_position = window._mini_overlay.pos() + QPoint(-80, 50)
    window._mini_overlay.move(dragged_position)
    window._show_mini_overlay()

    assert window._mini_overlay.pos() == dragged_position


def test_mini_overlay_sorts_rows_by_change_percent_descending(qtbot) -> None:
    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
    )
    qtbot.addWidget(window)
    window.add_monitor()
    window._symbol_input.setText("159142")
    window.add_monitor()
    window._symbol_input.setText("159325")
    window.add_monitor()
    window._sessions[0].name = "跌幅小"
    window._sessions[0].change_percent = -1.0
    window._sessions[1].name = "涨幅大"
    window._sessions[1].change_percent = 2.1
    window._sessions[2].name = "暂无涨跌"
    window._sessions[2].change_percent = None
    window._market_breadth = MarketBreadth("沪深京全A", 500, 4650, 60, -2.23)

    window._update_mini_overlay()

    labels = window._mini_overlay.findChildren(QLabel)
    assert [labels[index].text() for index in (0, 4, 8, 12)] == [
        "涨幅大",
        "跌幅小",
        "暂无涨跌",
        "沪深京全A",
    ]


def test_each_market_snapshot_updates_visible_unfinished_kline(qtbot) -> None:
    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
    )
    qtbot.addWidget(window)
    window.add_monitor()
    session = window._sessions[0]
    timestamp = datetime.now().astimezone().replace(second=0, microsecond=0)
    session.chart_series["intraday"] = ChartSeries(
        "159952",
        "测试标的",
        "intraday",
        (MarketBar(timestamp, 100, 100, 99, 100, 500),),
        98,
    )
    session.quote = MarketQuote(
        "159952",
        100,
        timestamp,
        name="测试标的",
        cumulative_volume=1_000,
    )
    window._chart_symbol = "159952"
    latest_quote = MarketQuote(
        "159952",
        101,
        timestamp.replace(second=1),
        name="测试标的",
        change_percent=3.06,
        cumulative_volume=1_020,
    )
    snapshot = MonitorSnapshot(
        quote=latest_quote,
        rsi=61.5,
        rsi_values={6: 61.5},
        signal=SignalCondition.NONE,
        notification_status="等待信号",
        connection_status="已连接",
        price_history=(99, 100, 101),
    )

    window._render_snapshot(0, session, snapshot)

    updated = session.chart_series["intraday"].bars[-1]
    assert updated.close == 101
    assert updated.high == 101
    assert updated.volume == 520
    assert window._intraday_chart._data is not None
    assert window._intraday_chart._data.series is not None
    assert window._intraday_chart._data.series.bars[-1].close == 101


def test_chart_refresh_populates_closed_quote_overview_without_monitoring(qtbot) -> None:
    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        return MonitorEngine(
            provider=OneQuoteProvider(symbol),
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="600839",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
    )
    qtbot.addWidget(window)
    window.add_monitor()
    session = window._sessions[0]
    window._chart_symbol = session.symbol
    timestamp = datetime.now().astimezone().replace(hour=15, minute=0)
    quote = MarketQuote(
        session.symbol,
        7.05,
        timestamp,
        name="四川长虹",
        is_trading=False,
        previous_close=6.86,
        open_price=6.87,
        high_price=7.26,
        low_price=6.85,
        cumulative_volume=1_672_941,
        cumulative_amount=1_174_935_764,
        bid_levels=((7.04, 7269),),
        ask_levels=((7.05, 9535),),
    )
    series = ChartSeries(
        session.symbol,
        quote.name,
        "intraday",
        (MarketBar(timestamp, 7.05, 7.05, 7.05, 7.05, 100),),
        quote.previous_close,
    )

    window._finish_chart_update(1, session.symbol, "intraday", series, quote, None, None)

    assert session.price == 7.05
    assert session.quote is not None
    assert session.quote.is_trading is False
    assert session.quote.bid_levels == ((7.04, 7269),)
    assert session.quote.ask_levels == ((7.05, 9535),)
    assert window._intraday_chart._data is not None
    assert window._intraday_chart._data.quote == session.quote


def test_display_quote_refresh_does_not_erase_previous_order_book() -> None:
    timestamp = datetime.now().astimezone()
    previous = MarketQuote(
        "600839",
        7.05,
        timestamp,
        name="四川长虹",
        previous_close=6.86,
        bid_levels=((7.04, 7269),),
        ask_levels=((7.05, 9535),),
    )
    current = MarketQuote("600839", 7.06, timestamp, name="四川长虹")

    merged = MainWindow._merge_display_quote(previous, current)

    assert merged.price == 7.06
    assert merged.previous_close == 6.86
    assert merged.bid_levels == previous.bid_levels
    assert merged.ask_levels == previous.ask_levels


def test_chart_worker_fetches_quote_and_series_for_the_display(qtbot) -> None:
    engine = MonitorEngine(
        provider=OneQuoteProvider("600839"),
        rsi_calculator=RsiCalculator(2),
        signal_detector=SignalDetector(30, 70),
        notifier=ConsoleNotificationService(),
        price_history_size=10,
    )
    session = MonitorSession(engine, "600839", "1min", 30, 70)
    worker = ChartUpdateWorker(7, session, "intraday", 240)
    results: list[tuple] = []
    worker.signals.finished.connect(lambda *args: results.append(args))

    worker.run()

    assert len(results) == 1
    request_id, symbol, period, series, quote, chart_error, quote_error = results[0]
    assert (request_id, symbol, period) == (7, "600839", "intraday")
    assert series.name == "测试标的"
    assert quote.price == 100.0
    assert chart_error is None
    assert quote_error is None


class BlockingQuoteProvider(MarketDataProvider):
    def __init__(self, symbol: str, started: Event, release: Event) -> None:
        self._symbol = symbol
        self._started = started
        self._release = release
        self.call_count = 0

    def fetch_latest_quote(self) -> MarketQuote:
        self.call_count += 1
        self._started.set()
        self._release.wait(timeout=2)
        return MarketQuote(self._symbol, 100.0, datetime.now().astimezone(), name="测试标的")


def test_slow_market_request_does_not_block_ui_or_repeat(qtbot) -> None:
    started = Event()
    release = Event()
    providers: list[BlockingQuoteProvider] = []

    def create_test_engine(symbol: str, period: str, buy: float, sell: float) -> MonitorEngine:
        provider = BlockingQuoteProvider(symbol, started, release)
        providers.append(provider)
        return MonitorEngine(
            provider=provider,
            rsi_calculator=RsiCalculator(2),
            signal_detector=SignalDetector(buy, sell),
            notifier=ConsoleNotificationService(),
            price_history_size=10,
        )

    window = MainWindow(
        engine_factory=create_test_engine,
        initial_symbol="159952",
        monitoring_periods=("1min",),
        default_monitoring_period="1min",
        default_buy_rsi_threshold=30,
        default_sell_rsi_threshold=70,
        test_notification_sender=lambda symbol, period: NotificationResult(True, "已发送"),
        system_tray_available=lambda: True,
    )
    qtbot.addWidget(window)
    window.show()
    window.add_monitor()

    window.start_monitoring()
    qtbot.waitUntil(started.is_set)
    window._poll_due_monitors()
    window._poll_due_monitors()

    assert window._stop_button.isEnabled()
    assert providers[0].call_count == 1

    window.close()
    assert window.isHidden()

    release.set()
    qtbot.waitUntil(lambda: not window._active_updates)
    window._allow_quit = True
    window.close()
