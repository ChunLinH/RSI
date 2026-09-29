from dataclasses import dataclass, field, replace
from datetime import datetime
import logging
import re
from collections.abc import Callable
from time import monotonic

from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QInputDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from rsi_monitor.market_data.base import (
    ChartSeries,
    MarketBar,
    MarketDataError,
    MarketQuote,
)
from rsi_monitor.market_data.breadth import MarketBreadth
from rsi_monitor.market_data.chart_periods import CHART_PERIODS
from rsi_monitor.market_data.live_series import apply_live_quote
from rsi_monitor.market_data.security_search import SecuritySearchResult
from rsi_monitor.monitoring.engine import MonitorEngine, MonitorSnapshot
from rsi_monitor.monitoring.session_store import MonitorDefinition
from rsi_monitor.notifications.base import NotificationResult
from rsi_monitor.ui.mini_overlay import MiniOverlay, MiniOverlayFooter, MiniOverlayRow
from rsi_monitor.ui.intraday_chart import IntradayChartPanel, MarketChartData
from rsi_monitor.ui.preferences import UiPreferences


@dataclass
class MonitorSession:
    engine: MonitorEngine
    symbol: str
    period: str
    buy_threshold: float
    sell_threshold: float
    name: str = ""
    rsi: float | None = None
    price: float | None = None
    change_percent: float | None = None
    timestamp: datetime | None = None
    price_history: tuple[float, ...] = ()
    opening_auction_bars: tuple[MarketBar, ...] = ()
    quote: MarketQuote | None = None
    chart_series: dict[str, ChartSeries] = field(default_factory=dict)


class MonitorUpdateSignals(QObject):
    finished = Signal(object, object, object)


class MonitorUpdateWorker(QRunnable):
    def __init__(self, session: MonitorSession) -> None:
        super().__init__()
        self._session = session
        self.signals = MonitorUpdateSignals()

    @Slot()
    def run(self) -> None:
        try:
            snapshot = self._session.engine.tick()
            error = None
        except Exception as caught_error:
            snapshot = None
            error = caught_error
        self.signals.finished.emit(self._session, snapshot, error)


class ChartUpdateSignals(QObject):
    finished = Signal(int, str, str, object, object, object, object)


class ChartUpdateWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        session: MonitorSession,
        period: str,
        limit: int,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._session = session
        self._period = period
        self._limit = limit
        self.signals = ChartUpdateSignals()

    @Slot()
    def run(self) -> None:
        try:
            quote = self._session.engine.fetch_display_quote()
            quote_error = None
        except Exception as caught_error:
            quote = None
            quote_error = caught_error
        try:
            series = self._session.engine.fetch_chart_series(self._period, self._limit)
            chart_error = None
        except Exception as caught_error:
            series = None
            chart_error = caught_error
        self.signals.finished.emit(
            self._request_id,
            self._session.symbol,
            self._period,
            series,
            quote,
            chart_error,
            quote_error,
        )


class MarketBreadthUpdateSignals(QObject):
    finished = Signal(object, object)


class MarketBreadthUpdateWorker(QRunnable):
    def __init__(self, fetcher: Callable[[], MarketBreadth]) -> None:
        super().__init__()
        self._fetcher = fetcher
        self.signals = MarketBreadthUpdateSignals()

    @Slot()
    def run(self) -> None:
        try:
            breadth = self._fetcher()
            error = None
        except Exception as caught_error:
            breadth = None
            error = caught_error
        self.signals.finished.emit(breadth, error)


class MainWindow(QMainWindow):
    def __init__(
        self,
        engine_factory: Callable[[str, str, float, float], MonitorEngine],
        initial_symbol: str,
        monitoring_periods: tuple[str, ...],
        default_monitoring_period: str,
        default_buy_rsi_threshold: float,
        default_sell_rsi_threshold: float,
        test_notification_sender: Callable[[str, str], NotificationResult],
        rsi_periods: tuple[int, ...] = (6,),
        signal_rsi_period: int = 6,
        refresh_interval_seconds: float = 1.0,
        saved_monitors: list[MonitorDefinition] | None = None,
        session_saver: Callable[[list[MonitorDefinition]], None] | None = None,
        system_tray_available: Callable[[], bool] | None = None,
        chart_data_limit: int = 240,
        initial_chart_auto_hide: bool = True,
        initial_chart_indicator: str = "rsi",
        initial_show_call_auction: bool = True,
        initial_quote_panel_collapsed: bool = False,
        preference_saver: Callable[[UiPreferences], None] | None = None,
        chart_refresh_interval_seconds: float = 15,
        market_breadth_fetcher: Callable[[], MarketBreadth] | None = None,
        market_breadth_refresh_interval_seconds: float = 15,
        security_searcher: Callable[[str], list[SecuritySearchResult]] | None = None,
    ) -> None:
        super().__init__()
        self._engine_factory = engine_factory
        self._sessions: list[MonitorSession] = []
        self._timer = QTimer(self)
        self._timer.setInterval(round(refresh_interval_seconds * 1_000))
        self._timer.timeout.connect(self._poll_due_monitors)
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(4)
        self._chart_thread_pool = QThreadPool(self)
        self._chart_thread_pool.setMaxThreadCount(2)
        self._active_updates: set[int] = set()
        self._initial_symbol = initial_symbol
        self._monitoring_periods = monitoring_periods
        self._default_monitoring_period = default_monitoring_period
        self._default_buy_rsi_threshold = default_buy_rsi_threshold
        self._default_sell_rsi_threshold = default_sell_rsi_threshold
        self._rsi_periods = rsi_periods
        self._signal_rsi_period = signal_rsi_period
        if signal_rsi_period not in rsi_periods:
            raise ValueError("信号 RSI 周期必须在显示 RSI 周期中")
        self._test_notification_sender = test_notification_sender
        self._session_saver = session_saver
        self._preference_saver = preference_saver
        self._chart_data_limit = chart_data_limit
        self._chart_request_id = 0
        self._active_chart_requests: set[tuple[str, str]] = set()
        self._chart_period_index = 0
        self._initial_chart_auto_hide = initial_chart_auto_hide
        self._initial_chart_indicator = initial_chart_indicator
        self._initial_show_call_auction = initial_show_call_auction
        self._initial_quote_panel_collapsed = initial_quote_panel_collapsed
        self._system_tray_available = system_tray_available or QSystemTrayIcon.isSystemTrayAvailable
        self._market_breadth_fetcher = market_breadth_fetcher
        self._market_breadth_refresh_interval_seconds = market_breadth_refresh_interval_seconds
        self._security_searcher = security_searcher
        self._market_breadth: MarketBreadth | None = None
        self._market_breadth_update_active = False
        self._next_market_breadth_update_at = 0.0
        self._tray_icon: QSystemTrayIcon | None = None
        self._allow_quit = False
        self._updating_table = False
        self._logger = logging.getLogger(__name__)

        self.setWindowTitle("股票 RSI 实时盯盘助手")
        self.resize(1180, 620)
        self._build_ui()
        self._mini_overlay = MiniOverlay(
            self._restore_from_mini_overlay,
            self._hide_mini_overlay,
            self.quit_application,
            self._toggle_intraday_chart,
        )
        self._intraday_chart = IntradayChartPanel(
            initial_chart_indicator,
            quote_panel_collapsed=initial_quote_panel_collapsed,
        )
        self._intraday_chart.set_auto_hide_on_focus_loss(initial_chart_auto_hide)
        self._intraday_chart.period_step_requested.connect(self._change_chart_period)
        self._intraday_chart.period_selected.connect(self._select_chart_period)
        self._intraday_chart.refresh_requested.connect(
            self._request_current_chart_series
        )
        self._intraday_chart.indicator_changed.connect(self._save_ui_preferences)
        self._intraday_chart.quote_panel_collapsed_changed.connect(
            self._quote_panel_collapsed_changed
        )
        self._auto_hide_chart_checkbox.toggled.connect(self._set_chart_auto_hide)
        self._show_call_auction_checkbox.toggled.connect(self._set_show_call_auction)
        self._mini_overlay.geometry_changed.connect(
            self._position_intraday_chart_if_visible
        )
        self._chart_symbol: str | None = None
        self._chart_refresh_timer = QTimer(self)
        self._chart_refresh_timer.setInterval(round(chart_refresh_interval_seconds * 1_000))
        self._chart_refresh_timer.timeout.connect(self._refresh_visible_chart)
        self._chart_refresh_timer.start()
        self._mini_overlay_positioned = False
        self._create_system_tray_icon()
        self._restore_saved_monitors(saved_monitors or [])

    def _build_ui(self) -> None:
        self._monitor_status = QLabel("已停止")
        self._symbol_input = QLineEdit(self._initial_symbol)
        self._symbol_input.setPlaceholderText("输入六位代码或中文名称")
        self._period_selector = QComboBox()
        self._period_selector.addItems(self._monitoring_periods)
        self._period_selector.setCurrentText(self._default_monitoring_period)
        self._buy_threshold = self._create_threshold_input(self._default_buy_rsi_threshold)
        self._sell_threshold = self._create_threshold_input(self._default_sell_rsi_threshold)
        self._notification_status = QLabel("--")
        self._auto_hide_chart_checkbox = QCheckBox("点击K线图外区域后自动关闭")
        self._auto_hide_chart_checkbox.setChecked(self._initial_chart_auto_hide)
        self._show_call_auction_checkbox = QCheckBox("显示集合竞价（09:15–09:25）")
        self._show_call_auction_checkbox.setChecked(self._initial_show_call_auction)
        self._events = QTextEdit()
        self._events.setReadOnly(True)

        controls = QFormLayout()
        controls.addRow("输入代码/名称：", self._symbol_input)
        controls.addRow("监控周期：", self._period_selector)
        controls.addRow(f"买入 RSI({self._signal_rsi_period}) ≤：", self._buy_threshold)
        controls.addRow(f"卖出 RSI({self._signal_rsi_period}) ≥：", self._sell_threshold)
        controls.addRow("监控状态：", self._monitor_status)
        controls.addRow("通知状态：", self._notification_status)
        controls.addRow("分时图设置：", self._auto_hide_chart_checkbox)
        controls.addRow("集合竞价：", self._show_call_auction_checkbox)

        self._table = QTableWidget(0, len(self._headers))
        self._table.setHorizontalHeaderLabels(self._headers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._load_selected_thresholds)
        self._table.itemChanged.connect(self._apply_table_threshold_change)

        self._add_button = QPushButton("添加监控")
        self._update_threshold_button = QPushButton("更新选中 RSI")
        self._remove_button = QPushButton("移除选中")
        self._start_button = QPushButton("开始全部监控")
        self._stop_button = QPushButton("停止全部监控")
        self._test_notification_button = QPushButton("测试通知")
        self._add_button.clicked.connect(self.add_monitor)
        self._update_threshold_button.clicked.connect(self.update_selected_thresholds)
        self._remove_button.clicked.connect(self.remove_selected_monitor)
        self._start_button.clicked.connect(self.start_monitoring)
        self._stop_button.clicked.connect(self.stop_monitoring)
        self._test_notification_button.clicked.connect(self.test_notification)
        self._stop_button.setEnabled(False)

        buttons = QHBoxLayout()
        for button in (
            self._add_button,
            self._update_threshold_button,
            self._remove_button,
            self._start_button,
            self._stop_button,
            self._test_notification_button,
        ):
            buttons.addWidget(button)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addLayout(buttons)
        layout.addWidget(self._table)
        layout.addWidget(QLabel("事件："))
        layout.addWidget(self._events)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def _create_system_tray_icon(self) -> None:
        if not self._system_tray_available():
            self._logger.warning("系统托盘不可用，关闭窗口将直接退出程序")
            return
        tray_icon = QSystemTrayIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self
        )
        tray_icon.setToolTip(self.windowTitle())
        tray_menu = QMenu(self)
        show_action = tray_menu.addAction("显示主窗口")
        show_action.triggered.connect(self.restore_from_tray)
        overlay_action = tray_menu.addAction("显示悬浮窗")
        overlay_action.triggered.connect(self._show_mini_overlay)
        quit_action = tray_menu.addAction("退出程序")
        quit_action.triggered.connect(self.quit_application)
        tray_icon.setContextMenu(tray_menu)
        tray_icon.activated.connect(self._handle_tray_activation)
        tray_icon.show()
        self._tray_icon = tray_icon

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_quit or self._tray_icon is None:
            self._mini_overlay.hide()
            self._intraday_chart.hide()
            self._chart_thread_pool.clear()
            if self._tray_icon is not None:
                self._tray_icon.hide()
            event.accept()
            return
        self._mini_overlay.hide()
        self._intraday_chart.hide()
        self.hide()
        event.ignore()
        self._tray_icon.showMessage(
            "股票 RSI 实时盯盘助手",
            "程序已缩小到系统托盘，监控和通知仍在运行。",
            QSystemTrayIcon.MessageIcon.Information,
            2_000,
        )

    def restore_from_tray(self) -> None:
        self._mini_overlay.hide()
        self._intraday_chart.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self) -> None:
        self._allow_quit = True
        self._timer.stop()
        self._chart_refresh_timer.stop()
        self._thread_pool.clear()
        self._chart_thread_pool.clear()
        self._mini_overlay.hide()
        self._intraday_chart.hide()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.close()
        QApplication.quit()

    def _handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason is QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_from_tray()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() is QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(150, self._show_mini_overlay_after_minimize)

    @property
    def has_system_tray(self) -> bool:
        return self._tray_icon is not None

    def add_monitor(self) -> None:
        query = self._symbol_input.text().strip()
        symbol = self._resolve_symbol(query)
        if symbol is None:
            return
        if any(session.symbol == symbol for session in self._sessions):
            self._append_event(f"{symbol} 已在监控列表中")
            return
        buy_threshold, sell_threshold = self._current_thresholds()
        if buy_threshold is None:
            return
        period = self._period_selector.currentText()
        try:
            engine = self._engine_factory(symbol, period, buy_threshold, sell_threshold)
        except (TypeError, ValueError) as error:
            self._logger.exception("无法创建 %s 监控引擎", symbol)
            self._append_event(f"添加失败：{error}")
            return
        self._sessions.append(MonitorSession(engine, symbol, period, buy_threshold, sell_threshold))
        self._render_table()
        self._table.selectRow(len(self._sessions) - 1)
        self._save_monitors()
        self._append_event(f"已添加 {symbol}（{period}）")
        if self._timer.isActive():
            self._poll_due_monitors()

    def _resolve_symbol(self, query: str) -> str | None:
        if not query:
            self._append_event("添加失败：请输入股票/ETF 代码或中文名称")
            return None
        if re.fullmatch(r"\d{6}", query):
            return query
        if self._security_searcher is None:
            self._append_event("添加失败：名称搜索不可用，请输入六位代码")
            return None

        try:
            results = self._security_searcher(query)
        except Exception as error:
            self._logger.exception("搜索证券名称失败：%s", query)
            self._append_event(f"搜索失败：{error}")
            return None
        if not results:
            self._append_event(f"未找到“{query}”对应的股票或 ETF")
            return None

        if len(results) == 1:
            selected = results[0]
        else:
            options = [f"{result.name}（{result.symbol}）" for result in results]
            selected_text, accepted = QInputDialog.getItem(
                self,
                "选择监控标的",
                f"“{query}”找到多个结果，请选择：",
                options,
                0,
                False,
            )
            if not accepted:
                return None
            selected = results[options.index(selected_text)]
        self._symbol_input.setText(selected.symbol)
        return selected.symbol

    def remove_selected_monitor(self) -> None:
        row = self._selected_row()
        if row is None:
            self._append_event("请先选择要移除的监控项")
            return
        symbol = self._sessions[row].symbol
        self._sessions.pop(row)
        self._render_table()
        self._save_monitors()
        self._append_event(f"已移除 {symbol}")
        self._refresh_monitor_status()

    def update_selected_thresholds(self) -> None:
        row = self._selected_row()
        if row is None:
            self._append_event("请先选择要更新 RSI 的监控项")
            return
        buy_threshold, sell_threshold = self._current_thresholds()
        if buy_threshold is None:
            return
        session = self._sessions[row]
        session.engine.update_signal_thresholds(buy_threshold, sell_threshold)
        session.buy_threshold = buy_threshold
        session.sell_threshold = sell_threshold
        self._set_threshold_cells(row, session)
        self._save_monitors()
        self._append_event(f"{session.symbol} RSI 阈值已更新")

    def start_monitoring(self) -> None:
        if not self._sessions:
            self._append_event("请先添加至少一个监控项")
            return
        self._timer.start()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._refresh_monitor_status()
        self._poll_due_monitors()

    def stop_monitoring(self) -> None:
        self._timer.stop()
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._monitor_status.setText("已停止")

    def test_notification(self) -> None:
        session = self._selected_session()
        symbol = session.symbol if session else self._symbol_input.text().strip().upper()
        period = session.period if session else self._period_selector.currentText()
        if not symbol:
            self._notification_status.setText("测试失败：请输入股票/ETF 代码")
            return
        try:
            result = self._test_notification_sender(symbol, period)
        except Exception as error:
            self._logger.exception("测试通知失败")
            self._notification_status.setText(f"测试失败：{error}")
            self._append_event(f"测试通知失败：{error}")
            return
        prefix = "测试通知成功" if result.sent else "测试通知未发送"
        self._notification_status.setText(f"{prefix}：{result.status}")
        self._append_event(f"{prefix}：{result.status}")

    def _poll_due_monitors(self) -> None:
        self._queue_market_breadth_update()
        for session in self._sessions:
            session_id = id(session)
            if session_id in self._active_updates:
                continue
            worker = MonitorUpdateWorker(session)
            worker.signals.finished.connect(self._finish_session_update)
            self._active_updates.add(session_id)
            self._thread_pool.start(worker)

    def _queue_market_breadth_update(self) -> None:
        if self._market_breadth_fetcher is None or self._market_breadth_update_active:
            return
        now = monotonic()
        if now < self._next_market_breadth_update_at:
            return
        worker = MarketBreadthUpdateWorker(self._market_breadth_fetcher)
        worker.signals.finished.connect(self._finish_market_breadth_update)
        self._market_breadth_update_active = True
        self._next_market_breadth_update_at = (
            now + self._market_breadth_refresh_interval_seconds
        )
        self._thread_pool.start(worker)

    @Slot(object, object)
    def _finish_market_breadth_update(
        self, breadth: MarketBreadth | None, error: Exception | None
    ) -> None:
        self._market_breadth_update_active = False
        if error is not None:
            self._logger.warning("全 A 涨跌家数获取失败：%s", error)
            return
        if breadth is None:
            return
        self._market_breadth = breadth
        self._update_mini_overlay()

    @Slot(object, object, object)
    def _finish_session_update(
        self,
        session: MonitorSession,
        snapshot: MonitorSnapshot | None,
        error: Exception | None,
    ) -> None:
        self._active_updates.discard(id(session))
        row = next(
            (index for index, current in enumerate(self._sessions) if current is session),
            None,
        )
        if row is None:
            return
        if isinstance(error, MarketDataError):
            self._logger.warning("%s 行情获取失败：%s", session.symbol, error)
            self._set_cell(row, self._connection_column, "连接失败，自动重试中")
            self._set_cell(row, self._notification_column, "未发送（数据不可用）")
            self._append_event(f"{session.symbol} 行情获取失败：{error}")
            return
        if error is not None:
            self._logger.error("%s 监控更新失败：%s", session.symbol, error)
            self._set_cell(row, self._connection_column, "连接异常，自动重试中")
            self._set_cell(row, self._notification_column, "未发送（数据不可用）")
            self._append_event(f"{session.symbol} 监控异常：{error}")
            return
        if snapshot is None:
            return
        self._render_snapshot(row, session, snapshot)

    def _render_snapshot(
        self, row: int, session: MonitorSession, snapshot: MonitorSnapshot
    ) -> None:
        quote = snapshot.quote
        previous_quote = session.quote
        values = (
            quote.symbol,
            quote.name or "名称暂不可用",
            session.period,
            f"{quote.price:.3f}",
            quote.timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
            *(self._format_rsi(snapshot.rsi_values.get(period)) for period in self._rsi_periods),
            f"{session.buy_threshold:.1f}",
            f"{session.sell_threshold:.1f}",
            snapshot.signal.value,
            snapshot.connection_status,
            snapshot.notification_status,
        )
        for column, value in enumerate(values):
            self._set_cell(row, column, value)
        session.name = quote.name or session.symbol
        session.rsi = snapshot.rsi
        session.price = quote.price
        session.change_percent = quote.change_percent
        session.timestamp = quote.timestamp
        session.price_history = snapshot.price_history
        session.opening_auction_bars = snapshot.opening_auction_bars
        session.quote = quote
        session.chart_series = {
            period: apply_live_quote(series, quote, previous_quote)
            for period, series in session.chart_series.items()
        }
        self._update_mini_overlay()
        self._update_intraday_chart()
        if snapshot.notification_status != "等待信号":
            self._append_event(
                f"{quote.symbol} {snapshot.signal.value}信号，通知{snapshot.notification_status}"
            )

    def _render_table(self) -> None:
        self._table.setRowCount(len(self._sessions))
        for row, session in enumerate(self._sessions):
            values = (
                session.symbol,
                "--",
                session.period,
                "--",
                "--",
                *("计算中" for _ in self._rsi_periods),
                f"{session.buy_threshold:.1f}",
                f"{session.sell_threshold:.1f}",
                "无信号",
                "未连接",
                "--",
            )
            for column, value in enumerate(values):
                self._set_cell(row, column, value)
        self._update_mini_overlay()
        self._update_intraday_chart()

    def _show_mini_overlay(self) -> None:
        self._queue_market_breadth_update()
        self._update_mini_overlay()
        if not self._mini_overlay_positioned:
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self._mini_overlay.move(
                    available.right() - self._mini_overlay.width() - 20,
                    available.top() + 60,
                )
                self._mini_overlay_positioned = True
        self._mini_overlay.show()
        self._mini_overlay.raise_()
        self.hide()

    def _show_mini_overlay_after_minimize(self) -> None:
        if self.isVisible() and self.isMinimized() and not self._allow_quit:
            self._show_mini_overlay()

    def _restore_from_mini_overlay(self) -> None:
        self._intraday_chart.hide()
        self.restore_from_tray()

    def _hide_mini_overlay(self) -> None:
        self._mini_overlay.hide()
        self._intraday_chart.hide()
        self.hide()

    def _update_mini_overlay(self) -> None:
        sessions = sorted(
            self._sessions,
            key=lambda session: (
                session.change_percent
                if session.change_percent is not None
                else float("-inf")
            ),
            reverse=True,
        )
        footer = (
            MiniOverlayFooter(
                name=self._market_breadth.name,
                up_count=self._market_breadth.up_count,
                down_count=self._market_breadth.down_count,
                flat_count=self._market_breadth.flat_count,
                change_percent=self._market_breadth.change_percent,
                turnover_amount=self._market_breadth.turnover_amount,
                previous_same_time_amount=(
                    self._market_breadth.previous_same_time_amount
                ),
            )
            if self._market_breadth is not None
            else None
        )
        self._mini_overlay.set_rows(
            [
                MiniOverlayRow(
                    symbol=session.symbol,
                    name=session.name or session.symbol,
                    rsi=session.rsi,
                    price=session.price,
                    change_percent=session.change_percent,
                )
                for session in sessions
            ],
            footer,
        )

    def _toggle_intraday_chart(self, symbol: str) -> None:
        self._intraday_chart.cancel_pending_outside_click_hide()
        if self._intraday_chart.isVisible() and self._chart_symbol == symbol:
            self._intraday_chart.hide()
            return
        self._chart_symbol = symbol
        self._update_intraday_chart()
        self._position_intraday_chart()
        self._intraday_chart.show()
        # 悬浮条的鼠标释放事件结束后，Windows 可能会把焦点还给原窗口。
        # 延迟到下一轮事件循环再激活图表，确保 PgUp/PgDn 直接交给图表。
        QTimer.singleShot(0, self._activate_intraday_chart)
        self._request_current_chart_series()

    def _activate_intraday_chart(self) -> None:
        if not self._intraday_chart.isVisible():
            return
        self._intraday_chart.raise_()
        self._intraday_chart.activateWindow()
        self._intraday_chart.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _update_intraday_chart(self) -> None:
        if self._chart_symbol is None:
            return
        session = next(
            (item for item in self._sessions if item.symbol == self._chart_symbol),
            None,
        )
        if session is None:
            self._intraday_chart.hide()
            self._chart_symbol = None
            return
        period = self._current_chart_period
        series = session.chart_series.get(period)
        if (
            series is not None
            and series.previous_close is None
            and session.quote is not None
            and session.quote.previous_close is not None
        ):
            series = replace(series, previous_close=session.quote.previous_close)
        if period == "intraday" and series is None and session.opening_auction_bars:
            previous_close = None
            if (
                session.price is not None
                and session.change_percent is not None
                and session.change_percent > -100
            ):
                previous_close = session.price / (1 + session.change_percent / 100)
            series = ChartSeries(
                symbol=session.symbol,
                name=session.name or session.symbol,
                period="intraday",
                bars=(),
                previous_close=previous_close,
                auction_bars=session.opening_auction_bars,
            )
        self._intraday_chart.set_data(
            MarketChartData(
                symbol=session.symbol,
                name=session.name or session.symbol,
                price=session.price,
                rsi=session.rsi,
                change_percent=session.change_percent,
                timestamp=session.timestamp,
                period=period,
                series=series,
                quote=session.quote,
                opening_auction_bars=session.opening_auction_bars,
                loading=(session.symbol, period) in self._active_chart_requests,
                show_call_auction=self._show_call_auction_checkbox.isChecked(),
            )
        )

    @property
    def _current_chart_period(self) -> str:
        return CHART_PERIODS[self._chart_period_index]

    @Slot(int)
    def _change_chart_period(self, step: int) -> None:
        self._chart_period_index = (self._chart_period_index + step) % len(CHART_PERIODS)
        self._update_intraday_chart()
        self._request_current_chart_series()
        self._intraday_chart.setFocus(Qt.FocusReason.ShortcutFocusReason)

    @Slot(str)
    def _select_chart_period(self, period: str) -> None:
        try:
            self._chart_period_index = CHART_PERIODS.index(period)
        except ValueError:
            return
        self._update_intraday_chart()
        self._request_current_chart_series()
        self._intraday_chart.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _refresh_visible_chart(self) -> None:
        if self._intraday_chart.isVisible():
            self._request_current_chart_series()

    def _request_current_chart_series(self) -> None:
        if self._chart_symbol is None:
            return
        session = next(
            (item for item in self._sessions if item.symbol == self._chart_symbol),
            None,
        )
        if session is None:
            return
        period = self._current_chart_period
        request_key = (session.symbol, period)
        if request_key in self._active_chart_requests:
            return
        self._chart_request_id += 1
        worker = ChartUpdateWorker(
            self._chart_request_id,
            session,
            period,
            self._chart_data_limit,
        )
        worker.signals.finished.connect(self._finish_chart_update)
        self._active_chart_requests.add(request_key)
        self._update_intraday_chart()
        self._chart_thread_pool.start(worker)

    @Slot(int, str, str, object, object, object, object)
    def _finish_chart_update(
        self,
        request_id: int,
        symbol: str,
        period: str,
        series: ChartSeries | None,
        quote: MarketQuote | None,
        chart_error: Exception | None,
        quote_error: Exception | None,
    ) -> None:
        self._active_chart_requests.discard((symbol, period))
        session = next((item for item in self._sessions if item.symbol == symbol), None)
        if session is None:
            return
        previous_quote = session.quote
        if quote is not None:
            quote = self._merge_display_quote(previous_quote, quote)
            session.quote = quote
            session.name = quote.name or session.name or session.symbol
            session.price = quote.price
            session.change_percent = quote.change_percent
            session.timestamp = quote.timestamp
            self._update_mini_overlay()
        elif quote_error is not None:
            self._logger.warning("%s 行情概览加载失败：%s", symbol, quote_error)
        if series is not None:
            session.chart_series[period] = (
                apply_live_quote(series, quote, previous_quote)
                if quote is not None
                else series
            )
            if not session.name:
                session.name = series.name
                self._update_mini_overlay()
        elif chart_error is not None:
            self._logger.warning("%s %s 图表加载失败：%s", symbol, period, chart_error)
        if self._chart_symbol != symbol or self._current_chart_period != period:
            return
        if chart_error is not None and period not in session.chart_series:
            self._intraday_chart.set_data(
                MarketChartData(
                    symbol=session.symbol,
                    name=session.name or session.symbol,
                    price=session.price,
                    rsi=session.rsi,
                    change_percent=session.change_percent,
                    timestamp=session.timestamp,
                    period=period,
                    error=str(chart_error),
                    quote=session.quote,
                    opening_auction_bars=session.opening_auction_bars,
                    show_call_auction=self._show_call_auction_checkbox.isChecked(),
                )
            )
            return
        self._update_intraday_chart()

    @staticmethod
    def _merge_display_quote(
        previous: MarketQuote | None, current: MarketQuote
    ) -> MarketQuote:
        """保留上次快照中本次接口暂未返回的概览字段和买卖五档。"""
        if previous is None:
            return current
        optional_fields = (
            "change_percent",
            "cumulative_volume",
            "cumulative_amount",
            "previous_close",
            "open_price",
            "high_price",
            "low_price",
            "price_change",
            "turnover_rate",
            "amplitude",
            "volume_ratio",
            "average_price",
            "circulating_market_cap",
            "total_market_cap",
            "pe_ratio",
            "pb_ratio",
            "inside_volume",
            "outside_volume",
            "upper_limit",
            "lower_limit",
        )
        replacements = {
            field_name: getattr(previous, field_name)
            for field_name in optional_fields
            if getattr(current, field_name) is None
            and getattr(previous, field_name) is not None
        }
        if not current.name and previous.name:
            replacements["name"] = previous.name
        if not current.bid_levels and previous.bid_levels:
            replacements["bid_levels"] = previous.bid_levels
        if not current.ask_levels and previous.ask_levels:
            replacements["ask_levels"] = previous.ask_levels
        return replace(current, **replacements) if replacements else current

    def _set_chart_auto_hide(self, enabled: bool) -> None:
        self._intraday_chart.set_auto_hide_on_focus_loss(enabled)
        self._save_ui_preferences()

    def _set_show_call_auction(self, enabled: bool) -> None:
        self._update_intraday_chart()
        self._save_ui_preferences()

    def _quote_panel_collapsed_changed(self, _collapsed: bool) -> None:
        self._position_intraday_chart_if_visible()
        self._save_ui_preferences()

    def _save_ui_preferences(self, *_args) -> None:
        if self._preference_saver is None:
            return
        try:
            self._preference_saver(
                UiPreferences(
                    chart_auto_hide=self._auto_hide_chart_checkbox.isChecked(),
                    chart_indicator=self._intraday_chart.indicator,
                    show_call_auction=self._show_call_auction_checkbox.isChecked(),
                    quote_panel_collapsed=self._intraday_chart.quote_panel_collapsed,
                )
            )
        except OSError as error:
            self._logger.warning("保存图表设置失败：%s", error)

    def _position_intraday_chart(self) -> None:
        screen = self._mini_overlay.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        gap = 10
        right_x = self._mini_overlay.frameGeometry().right() + gap
        if right_x + self._intraday_chart.width() <= available.right():
            x = right_x
        else:
            x = self._mini_overlay.frameGeometry().left() - self._intraday_chart.width() - gap
        x = max(available.left(), min(x, available.right() - self._intraday_chart.width()))
        y = max(
            available.top(),
            min(
                self._mini_overlay.frameGeometry().top(),
                available.bottom() - self._intraday_chart.height(),
            ),
        )
        self._intraday_chart.move(x, y)

    def _position_intraday_chart_if_visible(self) -> None:
        if self._intraday_chart.isVisible():
            self._position_intraday_chart()

    def _restore_saved_monitors(self, saved_monitors: list[MonitorDefinition]) -> None:
        for definition in saved_monitors:
            if any(session.symbol == definition.symbol for session in self._sessions):
                self._logger.warning("已忽略重复的已保存标的：%s", definition.symbol)
                continue
            try:
                engine = self._engine_factory(
                    definition.symbol,
                    definition.period,
                    definition.buy_threshold,
                    definition.sell_threshold,
                )
            except (TypeError, ValueError) as error:
                self._logger.warning("无法恢复 %s：%s", definition.symbol, error)
                self._append_event(f"无法恢复 {definition.symbol}：{error}")
                continue
            self._sessions.append(
                MonitorSession(
                    engine,
                    definition.symbol,
                    definition.period,
                    definition.buy_threshold,
                    definition.sell_threshold,
                )
            )
        self._render_table()
        if self._sessions:
            self._table.selectRow(0)
            self.start_monitoring()
            self._append_event(f"已恢复 {len(self._sessions)} 个监控项，已自动开始监控")

    def _save_monitors(self) -> None:
        if self._session_saver is None:
            return
        definitions = [
            MonitorDefinition(
                session.symbol,
                session.period,
                session.buy_threshold,
                session.sell_threshold,
            )
            for session in self._sessions
        ]
        try:
            self._session_saver(definitions)
        except OSError as error:
            self._logger.exception("保存监控清单失败")
            self._append_event(f"保存监控清单失败：{error}")

    def _load_selected_thresholds(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        self._buy_threshold.setValue(session.buy_threshold)
        self._sell_threshold.setValue(session.sell_threshold)

    def _current_thresholds(self) -> tuple[float, float] | tuple[None, None]:
        buy_threshold = self._buy_threshold.value()
        sell_threshold = self._sell_threshold.value()
        if buy_threshold >= sell_threshold:
            self._append_event("RSI 阈值无效：买入阈值必须小于卖出阈值")
            return None, None
        return buy_threshold, sell_threshold

    def _refresh_monitor_status(self) -> None:
        if self._timer.isActive():
            self._monitor_status.setText(f"监控中（{len(self._sessions)} 个标的）")

    def _selected_row(self) -> int | None:
        selected_rows = self._table.selectionModel().selectedRows()
        return selected_rows[0].row() if selected_rows else None

    def _selected_session(self) -> MonitorSession | None:
        row = self._selected_row()
        return self._sessions[row] if row is not None else None

    def _set_cell(self, row: int, column: int, value: str) -> None:
        self._updating_table = True
        try:
            item = self._table.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self._table.setItem(row, column, item)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if column in self._threshold_columns:
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            item.setText(value)
        finally:
            self._updating_table = False

    def _set_threshold_cells(self, row: int, session: MonitorSession) -> None:
        self._set_cell(row, self._buy_threshold_column, f"{session.buy_threshold:.1f}")
        self._set_cell(row, self._sell_threshold_column, f"{session.sell_threshold:.1f}")

    def _apply_table_threshold_change(self, item: QTableWidgetItem) -> None:
        if self._updating_table or item.column() not in self._threshold_columns:
            return
        row = item.row()
        if row >= len(self._sessions):
            return
        session = self._sessions[row]
        try:
            buy_threshold = float(self._table.item(row, self._buy_threshold_column).text())
            sell_threshold = float(self._table.item(row, self._sell_threshold_column).text())
        except (AttributeError, ValueError):
            self._set_threshold_cells(row, session)
            self._append_event(f"{session.symbol} 阈值无效：请输入数字")
            return
        if not 0 <= buy_threshold < sell_threshold <= 100:
            self._set_threshold_cells(row, session)
            self._append_event(f"{session.symbol} 阈值无效：买入必须小于卖出，且范围为 0 到 100")
            return
        session.engine.update_signal_thresholds(buy_threshold, sell_threshold)
        session.buy_threshold = buy_threshold
        session.sell_threshold = sell_threshold
        self._buy_threshold.setValue(buy_threshold)
        self._sell_threshold.setValue(sell_threshold)
        self._save_monitors()
        self._append_event(f"{session.symbol} RSI 阈值已直接更新")

    @staticmethod
    def _format_rsi(rsi: float | None) -> str:
        return "计算中" if rsi is None else f"{rsi:.2f}"

    @property
    def _headers(self) -> tuple[str, ...]:
        return (
            "代码",
            "名称",
            "周期",
            "最新价",
            "数据更新时间",
            *(f"RSI({period})" for period in self._rsi_periods),
            f"买入 RSI({self._signal_rsi_period}) ≤",
            f"卖出 RSI({self._signal_rsi_period}) ≥",
            "当前信号",
            "连接状态",
            "通知状态",
        )

    @property
    def _buy_threshold_column(self) -> int:
        return 5 + len(self._rsi_periods)

    @property
    def _sell_threshold_column(self) -> int:
        return self._buy_threshold_column + 1

    @property
    def _threshold_columns(self) -> tuple[int, int]:
        return self._buy_threshold_column, self._sell_threshold_column

    @property
    def _connection_column(self) -> int:
        return self._sell_threshold_column + 2

    @property
    def _notification_column(self) -> int:
        return self._sell_threshold_column + 3

    @staticmethod
    def _create_threshold_input(value: float) -> QDoubleSpinBox:
        input_box = QDoubleSpinBox()
        input_box.setRange(0, 100)
        input_box.setDecimals(1)
        input_box.setSingleStep(0.5)
        input_box.setValue(value)
        return input_box

    def _append_event(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._events.append(f"[{timestamp}] {message}")
