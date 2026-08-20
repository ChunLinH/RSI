from dataclasses import dataclass
from datetime import datetime, time as clock_time
from math import ceil

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton, QWidget

from rsi_monitor.indicators.series import (
    calculate_kdj_series,
    calculate_macd_series,
    calculate_rsi_series,
    calculate_sma_series,
)
from rsi_monitor.market_data.base import ChartSeries, MarketBar, MarketQuote
from rsi_monitor.market_data.chart_periods import (
    CHART_PERIOD_LABELS,
    CHART_PERIODS,
)


@dataclass(frozen=True)
class MarketChartData:
    symbol: str
    name: str
    price: float | None
    rsi: float | None
    change_percent: float | None
    timestamp: datetime | None
    period: str
    series: ChartSeries | None = None
    loading: bool = False
    error: str = ""
    show_call_auction: bool = True
    opening_auction_bars: tuple[MarketBar, ...] = ()
    quote: MarketQuote | None = None


class IntradayChartPanel(QWidget):
    """支持分时、K 线、成交量和技术指标的置顶行情图表。"""

    _AUCTION_WIDTH_RATIO = 0.08
    _CLICK_PERIODS = ("intraday", "five_day", "day", "week", "month")
    _INDICATORS = ("rsi", "macd", "kdj")
    _MAIN_CARD_WIDTH = 720.0
    _EXPANDED_WIDTH = 960
    _COMPACT_WIDTH = 720
    _WINDOW_HEIGHT = 560
    _QUICK_PERIODS = (
        ("intraday", "分时"),
        ("5min", "5分"),
        ("15min", "15分"),
        ("day", "日K"),
        ("week", "周K"),
    )

    period_step_requested = Signal(int)
    period_selected = Signal(str)
    indicator_changed = Signal(str)
    quote_panel_collapsed_changed = Signal(bool)
    refresh_requested = Signal()

    def __init__(
        self, indicator: str = "rsi", quote_panel_collapsed: bool = False
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setWindowTitle("RSI 行情图表")
        self.resize(
            self._COMPACT_WIDTH if quote_panel_collapsed else self._EXPANDED_WIDTH,
            self._WINDOW_HEIGHT,
        )
        self.setMinimumSize(self._COMPACT_WIDTH, self._WINDOW_HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._data: MarketChartData | None = None
        self._mouse_position: QPointF | None = None
        self._zoom_ranges: dict[str, tuple[int, int, int]] = {}
        self._pan_start_position: QPointF | None = None
        self._pan_start_range: tuple[int, int, int] | None = None
        self._is_panning = False
        self._period_tab_rects: dict[str, QRectF] = {}
        self._indicator_button_rects: dict[str, QRectF] = {}
        self._refresh_rect = QRectF()
        self._click_press_position: QPointF | None = None
        self._click_press_region: str | None = None
        self._click_press_button = Qt.MouseButton.NoButton
        self._quote_panel_collapsed = quote_panel_collapsed
        self._close_rect = QRectF()
        self._auto_hide_on_focus_loss = True
        self._focus_loss_timer = QTimer(self)
        self._focus_loss_timer.setSingleShot(True)
        self._focus_loss_timer.setInterval(80)
        self._focus_loss_timer.timeout.connect(self._hide_if_inactive)
        self._indicator_selector = QComboBox(self)
        self._indicator_selector.addItem("RSI", "rsi")
        self._indicator_selector.addItem("MACD", "macd")
        self._indicator_selector.addItem("KDJ", "kdj")
        self._indicator_selector.currentIndexChanged.connect(self._emit_indicator_changed)
        self._period_selector = QComboBox(self)
        for period in CHART_PERIODS:
            self._period_selector.addItem(CHART_PERIOD_LABELS[period], period)
        self._period_selector.currentIndexChanged.connect(self._emit_period_selected)
        self._quote_panel_toggle = QToolButton(self)
        self._quote_panel_toggle.setAutoRaise(True)
        self._quote_panel_toggle.setToolTip("显示或隐藏右侧行情详情")
        self._quote_panel_toggle.clicked.connect(self._toggle_quote_panel)
        self._quote_panel_toggle.setStyleSheet(
            "QToolButton { border: none; color: #737b84; font-size: 16px; }"
            "QToolButton:hover { background: #eef1f4; border-radius: 5px; }"
        )
        self._indicator_selector.setStyleSheet(
            "QComboBox { border: 1px solid #d9dde2; border-radius: 7px;"
            " padding: 2px 8px; background: #ffffff; }"
        )
        self._event_filter_installed = False
        self.set_indicator(indicator)
        self._update_quote_panel_toggle()

    @property
    def indicator(self) -> str:
        return str(self._indicator_selector.currentData())

    def set_indicator(self, indicator: str) -> None:
        index = self._indicator_selector.findData(indicator)
        if index < 0:
            raise ValueError(f"不支持的图表指标：{indicator}")
        self._indicator_selector.blockSignals(True)
        self._indicator_selector.setCurrentIndex(index)
        self._indicator_selector.blockSignals(False)
        self.update()

    def set_auto_hide_on_focus_loss(self, enabled: bool) -> None:
        self._auto_hide_on_focus_loss = enabled

    def set_data(self, data: MarketChartData) -> None:
        changed_chart = (
            self._data is None
            or self._data.symbol != data.symbol
            or self._data.period != data.period
        )
        self._data = data
        if changed_chart:
            self._pan_start_position = None
            self._pan_start_range = None
            self._is_panning = False
        index = self._period_selector.findData(data.period)
        if index >= 0 and index != self._period_selector.currentIndex():
            self._period_selector.blockSignals(True)
            self._period_selector.setCurrentIndex(index)
            self._period_selector.blockSignals(False)
        self._update_quote_panel_toggle()
        self.update()

    @property
    def quote_panel_collapsed(self) -> bool:
        return self._quote_panel_collapsed

    def set_quote_panel_collapsed(self, collapsed: bool) -> None:
        if self._quote_panel_collapsed == collapsed:
            return
        self._quote_panel_collapsed = collapsed
        self.resize(
            self._COMPACT_WIDTH if collapsed else self._EXPANDED_WIDTH,
            self._WINDOW_HEIGHT,
        )
        self._update_quote_panel_toggle()
        self.quote_panel_collapsed_changed.emit(collapsed)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._period_selector.hide()
        self._indicator_selector.setGeometry(
            int(self._MAIN_CARD_WIDTH - 104), self.height() - 30, 92, 24
        )
        self._update_quote_panel_toggle()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        application = QApplication.instance()
        if application is not None and not self._event_filter_installed:
            application.installEventFilter(self)
            self._event_filter_installed = True

    def hideEvent(self, event) -> None:
        application = QApplication.instance()
        if application is not None and self._event_filter_installed:
            application.removeEventFilter(self)
            self._event_filter_installed = False
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f1f3f6"))
        self._draw_window_cards(painter)
        self._close_rect = QRectF(self.width() - 31, 8, 22, 22)
        painter.setPen(QPen(QColor("#7a8189"), 1.3))
        painter.drawText(self._close_rect, Qt.AlignmentFlag.AlignCenter, "×")

        if self._data is None:
            painter.drawText(
                QRectF(0, 0, self._MAIN_CARD_WIDTH, self.height()),
                Qt.AlignmentFlag.AlignCenter,
                "暂无行情数据",
            )
            return

        self._draw_header(painter, self._data)
        self._draw_toolbar(painter, self._data)
        main_rect, volume_rect, indicator_rect = self._chart_rectangles()
        self._draw_quote_panel(painter, self._data, main_rect, indicator_rect)
        series = self._data.series
        all_bars = (
            self._visible_bars(
                series,
                self._data.show_call_auction,
                self._data.opening_auction_bars,
                len(series.bars),
            )
            if series is not None
            else ()
        )
        if series is not None and series.period not in {"intraday", "five_day"}:
            visible_start, visible_end = self._visible_range(
                series.period, len(all_bars)
            )
        else:
            visible_start, visible_end = 0, len(all_bars)
        bars = all_bars[visible_start:visible_end]
        if series is None or not bars:
            self._draw_empty_state(painter, main_rect, self._data)
            self._draw_grid(painter, volume_rect, horizontal=2, vertical=6)
            self._draw_grid(painter, indicator_rect, horizontal=3, vertical=6)
        else:
            x_positions = self._bar_x_positions(main_rect, bars, series.period)
            minimum, maximum = self._price_range(
                bars,
                series,
                all_bars=all_bars,
                visible_start=visible_start,
            )
            self._draw_price_chart(
                painter,
                main_rect,
                bars,
                all_bars,
                visible_start,
                series,
                x_positions,
                minimum,
                maximum,
            )
            display_index = self._display_bar_index(main_rect, x_positions)
            self._draw_volume(
                painter,
                volume_rect,
                bars,
                series,
                x_positions,
                display_index,
            )
            self._draw_indicator(
                painter,
                indicator_rect,
                bars,
                all_bars,
                visible_start,
                x_positions,
            )
            if series.period == "intraday" and any(
                bar.timestamp.time() < clock_time(9, 30) for bar in bars
            ):
                separator_x = (
                    main_rect.left()
                    + main_rect.width() * self._AUCTION_WIDTH_RATIO
                )
                painter.setPen(QPen(QColor("#aeb4bc"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(
                    QPointF(separator_x, main_rect.top()),
                    QPointF(separator_x, volume_rect.bottom()),
                )
                painter.setPen(QColor("#666666"))
                painter.drawText(main_rect.left() + 5, main_rect.top() + 35, "竞价")
            self._draw_hover(
                painter,
                main_rect,
                indicator_rect,
                bars,
                all_bars,
                visible_start,
                series,
                x_positions,
                minimum,
                maximum,
            )
        self._draw_footer(painter, self._data)

    def _draw_header(self, painter: QPainter, data: MarketChartData) -> None:
        painter.save()
        title_font = painter.font()
        title_font.setPointSizeF(title_font.pointSizeF() + 3)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#252a30"))
        price_text = "--" if data.price is None else f"{data.price:.3f}"
        change_text = "--" if data.change_percent is None else f"{data.change_percent:+.2f}%"
        time_text = (
            "--"
            if data.timestamp is None
            else data.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        )
        title = f"{data.name} ({data.symbol})"
        painter.drawText(12, 30, title)
        x = 12 + painter.fontMetrics().horizontalAdvance(title) + 14
        info_font = painter.font()
        info_font.setPointSizeF(max(info_font.pointSizeF() - 3, 8))
        info_font.setBold(False)
        painter.setFont(info_font)
        painter.setPen(QColor("#8a9098"))
        painter.drawText(x, 30, "最新")
        x += painter.fontMetrics().horizontalAdvance("最新") + 6
        change_color = (
            QColor("#d6575f")
            if (data.change_percent or 0) >= 0
            else QColor("#2f956d")
        )
        painter.setPen(change_color)
        price_change = ""
        if data.quote is not None:
            price_change_value = data.quote.price_change
            if price_change_value is None and data.quote.previous_close is not None:
                price_change_value = data.quote.price - data.quote.previous_close
            price_change = f" {self._signed(price_change_value)}"
        painter.drawText(x, 30, f"{price_text}{price_change}  {change_text}")
        painter.setPen(QColor("#646b73"))
        painter.drawText(
            QRectF(self._MAIN_CARD_WIDTH - 225, 10, 165, 28),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            time_text,
        )
        self._refresh_rect = QRectF(self._MAIN_CARD_WIDTH - 58, 12, 24, 26)
        painter.setPen(QColor("#7c848c"))
        painter.drawText(self._refresh_rect, Qt.AlignmentFlag.AlignCenter, "⟳")
        if data.series is not None and data.series.is_stale:
            painter.setPen(QColor("#ad6800"))
            painter.drawText(self._MAIN_CARD_WIDTH - 235, 30, "缓存")
        painter.restore()

    def _draw_window_cards(self, painter: QPainter) -> None:
        self._draw_card(
            painter,
            QRectF(0.5, 0.5, self._MAIN_CARD_WIDTH - 1, self.height() - 1),
        )
        if self._quote_panel_visible():
            self._draw_card(painter, self._overview_rect())
            self._draw_card(painter, self._order_book_rect())

    @staticmethod
    def _draw_card(painter: QPainter, rect: QRectF) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        painter.fillPath(path, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#dfe3e8"), 1))
        painter.drawPath(path)

    def _draw_toolbar(self, painter: QPainter, data: MarketChartData) -> None:
        painter.setPen(QPen(QColor("#e3e6ea"), 1))
        painter.drawLine(QPointF(1, 52), QPointF(self._MAIN_CARD_WIDTH - 1, 52))
        painter.drawLine(QPointF(1, 90), QPointF(self._MAIN_CARD_WIDTH - 1, 90))
        self._period_tab_rects.clear()
        self._indicator_button_rects.clear()
        font = painter.font()
        font.setPointSizeF(max(font.pointSizeF(), 9))
        painter.setFont(font)
        x = 10.0
        for period, label in self._QUICK_PERIODS:
            width = painter.fontMetrics().horizontalAdvance(label) + 18
            rect = QRectF(x, 59, width, 25)
            self._period_tab_rects[period] = rect
            active = data.period == period
            painter.setPen(QColor("#52758c") if active else QColor("#59616a"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            if active:
                painter.setPen(QPen(QColor("#52758c"), 2))
                painter.drawLine(
                    QPointF(rect.left() + 8, rect.bottom() + 3),
                    QPointF(rect.right() - 8, rect.bottom() + 3),
                )
            x = rect.right() + 4
        x += 12
        volume_rect = QRectF(x, 59, 70, 25)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e9edf1"))
        painter.drawRoundedRect(volume_rect, 5, 5)
        painter.setPen(QColor("#59616a"))
        painter.drawText(volume_rect, Qt.AlignmentFlag.AlignCenter, "▥ 成交量")
        x = volume_rect.right() + 7
        for indicator, label in (("rsi", "RSI"), ("macd", "MACD")):
            rect = QRectF(x, 59, 49 if indicator == "rsi" else 60, 25)
            self._indicator_button_rects[indicator] = rect
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor("#dfe6eb")
                if self.indicator == indicator
                else QColor("#eef1f4")
            )
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QColor("#4e565f"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            x = rect.right() + 7

    def _chart_rectangles(self) -> tuple[QRectF, QRectF, QRectF]:
        left = 48.0
        right = self._MAIN_CARD_WIDTH - (
            52.0 if self._data and self._data.period == "intraday" else 16.0
        )
        main_top = 94.0
        footer_top = self.height() - 34.0
        available_height = footer_top - main_top - 10
        main_height = available_height * 0.56
        volume_height = available_height * 0.16
        indicator_height = available_height - main_height - volume_height - 10
        main = QRectF(left, main_top, right - left, main_height)
        volume = QRectF(left, main.bottom() + 5, right - left, volume_height)
        indicator = QRectF(left, volume.bottom() + 5, right - left, indicator_height)
        return main, volume, indicator

    def _quote_panel_visible(self) -> bool:
        return not self._quote_panel_collapsed

    def _overview_rect(self) -> QRectF:
        return QRectF(
            self._MAIN_CARD_WIDTH + 8,
            0.5,
            self.width() - self._MAIN_CARD_WIDTH - 8.5,
            206,
        )

    def _order_book_rect(self) -> QRectF:
        return QRectF(
            self._MAIN_CARD_WIDTH + 8,
            214,
            self.width() - self._MAIN_CARD_WIDTH - 8.5,
            self.height() - 214.5,
        )

    def _update_quote_panel_toggle(self) -> None:
        self._quote_panel_toggle.setVisible(self._data is not None)
        self._quote_panel_toggle.setText("⚙")
        self._quote_panel_toggle.setGeometry(
            int(self._MAIN_CARD_WIDTH - 40), 58, 28, 27
        )

    def _toggle_quote_panel(self, _checked: bool = False) -> None:
        self.set_quote_panel_collapsed(not self._quote_panel_collapsed)

    def _draw_quote_panel(
        self,
        painter: QPainter,
        data: MarketChartData,
        main_chart: QRectF,
        indicator_chart: QRectF,
    ) -> None:
        if not self._quote_panel_visible():
            return
        overview = self._overview_rect()
        order_book = self._order_book_rect()
        painter.save()
        title_font = painter.font()
        title_font.setPointSizeF(title_font.pointSizeF() + 2)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#30353b"))
        overview_title = (
            "ETF概览" if "ETF" in data.name.upper() else "行情概览"
        )
        painter.drawText(overview.left() + 12, overview.top() + 29, overview_title)
        painter.drawText(order_book.left() + 12, order_book.top() + 29, "五档买卖")
        painter.setPen(QPen(QColor("#e3e6ea"), 1))
        painter.drawLine(
            QPointF(overview.left() + 1, overview.top() + 48),
            QPointF(overview.right() - 1, overview.top() + 48),
        )
        painter.drawLine(
            QPointF(order_book.left() + 1, order_book.top() + 48),
            QPointF(order_book.right() - 1, order_book.top() + 48),
        )
        painter.restore()

        if data.quote is None:
            painter.setPen(QColor("#979da5"))
            painter.drawText(overview, Qt.AlignmentFlag.AlignCenter, "暂无行情概览")
            painter.drawText(order_book, Qt.AlignmentFlag.AlignCenter, "暂无五档行情")
            return
        quote = data.quote
        rows = (
            ("今开", self._price(quote.open_price), "昨收", self._price(quote.previous_close)),
            ("最高", self._price(quote.high_price), "最低", self._price(quote.low_price)),
            ("成交量", self._hands(quote.cumulative_volume), "成交额", self._amount(quote.cumulative_amount)),
            ("换手率", self._percent(quote.turnover_rate), "振幅", self._percent(quote.amplitude)),
        )
        row_y = overview.top() + 71
        for left_label, left_value, right_label, right_value in rows:
            self._draw_overview_row(
                painter,
                overview,
                row_y,
                left_label,
                left_value,
                right_label,
                right_value,
            )
            row_y += 34

        asks = tuple(reversed(tuple(enumerate(quote.ask_levels, 1))))
        bids = tuple(enumerate(quote.bid_levels, 1))
        levels = (*asks, *bids)
        if not levels:
            painter.setPen(QColor("#979da5"))
            painter.drawText(order_book, Qt.AlignmentFlag.AlignCenter, "暂无五档行情")
            return
        maximum_volume = max(volume for _, (_, volume) in levels)
        row_height = 25.5
        row_y = order_book.top() + 67
        for side, side_levels in (("卖", asks), ("买", bids)):
            for level, (price, volume) in side_levels:
                self._draw_order_book_row(
                    painter,
                    order_book,
                    row_y,
                    side,
                    level,
                    price,
                    volume,
                    maximum_volume,
                )
                row_y += row_height

    @staticmethod
    def _draw_overview_row(
        painter: QPainter,
        panel: QRectF,
        row_y: float,
        left_label: str,
        left_value: str,
        right_label: str,
        right_value: str,
    ) -> None:
        content_left = panel.left() + 10
        center_gap = 8
        column_width = (panel.width() - 20 - center_gap) / 2
        for column, label, value in (
            (0, left_label, left_value),
            (1, right_label, right_value),
        ):
            left = content_left + column * (column_width + center_gap)
            painter.setPen(QColor("#777e86"))
            painter.drawText(
                QRectF(left, row_y - 14, 34, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            painter.setPen(QColor("#30353b"))
            painter.drawText(
                QRectF(left + 36, row_y - 14, column_width - 36, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                value,
            )

    def _draw_order_book_row(
        self,
        painter: QPainter,
        panel: QRectF,
        row_y: float,
        side: str,
        level: int,
        price: float,
        volume: float,
        maximum_volume: float,
    ) -> None:
        color = QColor("#b4525d") if side == "卖" else QColor("#2e8b69")
        bar_color = QColor("#f3c5ca") if side == "卖" else QColor("#bfe6d6")
        painter.setPen(QColor("#30353b"))
        painter.drawText(panel.left() + 12, row_y, f"{side}{self._level_name(level)}")
        painter.setPen(color)
        painter.drawText(panel.left() + 66, row_y, f"{price:.3f}")
        bar_width = 54 * volume / maximum_volume if maximum_volume > 0 else 0
        painter.fillRect(
            QRectF(panel.left() + 120, row_y - 15, bar_width, 18), bar_color
        )
        painter.setPen(QColor("#30353b"))
        painter.drawText(
            QRectF(panel.right() - 58, row_y - 15, 46, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self._hands(volume),
        )

    @staticmethod
    def _price(value: float | None) -> str:
        return "--" if value is None else f"{value:.3f}"

    @staticmethod
    def _number(value: float | None) -> str:
        return "--" if value is None else f"{value:.2f}"

    @staticmethod
    def _signed(value: float | None) -> str:
        return "--" if value is None else f"{value:+.3f}"

    @staticmethod
    def _percent(value: float | None) -> str:
        return "--" if value is None else f"{value:+.2f}%"

    @staticmethod
    def _hands(value: float | None) -> str:
        if value is None:
            return "--"
        if abs(value) >= 100_000_000:
            return f"{value / 100_000_000:.2f}亿"
        if abs(value) >= 10_000:
            return f"{value / 10_000:.2f}万"
        return f"{value:.0f}"

    @staticmethod
    def _amount(value: float | None) -> str:
        if value is None:
            return "--"
        if abs(value) >= 100_000_000:
            return f"{value / 100_000_000:.2f}亿"
        if abs(value) >= 10_000:
            return f"{value / 10_000:.2f}万"
        return f"{value:.0f}"

    @staticmethod
    def _market_cap(value: float | None) -> str:
        return "--" if value is None else f"{value:.2f}亿"

    @staticmethod
    def _shares(market_cap: float | None, price: float) -> str:
        if market_cap is None or price <= 0:
            return "--"
        return f"{market_cap / price:.2f}亿"

    @staticmethod
    def _level_name(level: int) -> str:
        return ("一", "二", "三", "四", "五")[level - 1]

    def _draw_empty_state(
        self, painter: QPainter, chart: QRectF, data: MarketChartData
    ) -> None:
        self._draw_grid(painter, chart, horizontal=4, vertical=6)
        painter.setPen(QColor("#777777"))
        if data.loading:
            message = "正在加载行情图表…"
        elif data.error:
            message = f"图表加载失败：{data.error}"
        else:
            message = "暂无图表数据"
        painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, message)

    def _draw_price_chart(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_bars: tuple[MarketBar, ...],
        visible_start: int,
        series: ChartSeries,
        x_positions: tuple[float, ...],
        minimum: float,
        maximum: float,
    ) -> None:
        self._draw_grid(
            painter,
            chart,
            horizontal=4 if series.period == "intraday" else 5,
            vertical=6,
        )
        self._draw_price_labels(painter, chart, minimum, maximum, series)
        if series.previous_close is not None:
            y = self._value_y(series.previous_close, chart, minimum, maximum)
            painter.setPen(QPen(QColor("#8b8f96"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        if series.period in {"intraday", "five_day"}:
            self._draw_trend_lines(
                painter,
                chart,
                bars,
                x_positions,
                minimum,
                maximum,
                fill_area=series.period == "intraday",
            )
        else:
            self._draw_candles(
                painter, chart, bars, x_positions, minimum, maximum
            )
            if series.period in {"day", "week", "month"}:
                self._draw_moving_averages(
                    painter,
                    chart,
                    bars,
                    all_bars,
                    visible_start,
                    x_positions,
                    minimum,
                    maximum,
                )
        self._draw_time_labels(painter, chart, bars, series.period, x_positions)

    def _draw_trend_lines(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        x_positions: tuple[float, ...],
        minimum: float,
        maximum: float,
        fill_area: bool,
    ) -> None:
        prices = tuple(bar.close for bar in bars)
        points = tuple(
            QPointF(x, self._value_y(price, chart, minimum, maximum))
            for x, price in zip(x_positions, prices)
        )
        if fill_area and points:
            area = QPainterPath(points[0])
            for point in points[1:]:
                area.lineTo(point)
            area.lineTo(points[-1].x(), chart.bottom())
            area.lineTo(points[0].x(), chart.bottom())
            area.closeSubpath()
            gradient = QLinearGradient(0, chart.top(), 0, chart.bottom())
            gradient.setColorAt(0, QColor(190, 218, 237, 125))
            gradient.setColorAt(1, QColor(231, 242, 249, 45))
            painter.fillPath(area, QBrush(gradient))
        cumulative_volume = 0.0
        cumulative_amount = 0.0
        averages: list[float | None] = []
        for bar in bars:
            cumulative_volume += bar.volume
            cumulative_amount += bar.amount or 0.0
            averages.append(
                cumulative_amount / cumulative_volume / 100
                if cumulative_volume > 0 and cumulative_amount > 0
                else None
            )
        if any(value is not None for value in averages):
            painter.setPen(QPen(QColor("#c2a459"), 1.1))
            self._draw_series(
                painter, averages, x_positions, chart, minimum, maximum
            )
        # 价格线最后绘制，避免与均价重合时被均价线遮住。
        painter.setPen(QPen(QColor("#4b7b98"), 1.25))
        self._draw_series(
            painter, prices, x_positions, chart, minimum, maximum
        )
        painter.setPen(QColor("#4b7b98"))
        painter.drawText(chart.left() + 8, chart.top() + 18, "价格")
        painter.setPen(QColor("#c2a459"))
        painter.drawText(chart.left() + 55, chart.top() + 18, "均价")

    def _draw_candles(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        x_positions: tuple[float, ...],
        minimum: float,
        maximum: float,
    ) -> None:
        body_width = self._bar_body_width(chart, x_positions, len(bars))
        for bar, x in zip(bars, x_positions):
            color = QColor("#e53935") if bar.close >= bar.open else QColor("#269a49")
            painter.setPen(QPen(color, 1))
            painter.drawLine(
                QPointF(x, self._value_y(bar.high, chart, minimum, maximum)),
                QPointF(x, self._value_y(bar.low, chart, minimum, maximum)),
            )
            open_y = self._value_y(bar.open, chart, minimum, maximum)
            close_y = self._value_y(bar.close, chart, minimum, maximum)
            body = QRectF(
                x - body_width / 2,
                min(open_y, close_y),
                body_width,
                max(abs(close_y - open_y), 1),
            )
            if bar.close >= bar.open:
                painter.fillRect(body, QColor("#ffffff"))
                painter.drawRect(body)
            else:
                painter.fillRect(body, color)

    def _draw_moving_averages(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_bars: tuple[MarketBar, ...],
        visible_start: int,
        x_positions: tuple[float, ...],
        minimum: float,
        maximum: float,
    ) -> None:
        closes = tuple(bar.close for bar in all_bars)
        series = (
            (5, QColor("#222222")),
            (10, QColor("#f39c12")),
            (20, QColor("#2979ff")),
            (60, QColor("#7e57c2")),
        )
        legend_x = chart.left() + 8
        for period, color in series:
            values = self._visible_moving_average_values(
                closes, period, visible_start, len(bars)
            )
            painter.setPen(QPen(color, 1.1))
            self._draw_series(
                painter, values, x_positions, chart, minimum, maximum
            )
            painter.drawText(legend_x, chart.top() + 18, f"MA{period}")
            legend_x += 52

    @staticmethod
    def _visible_moving_average_values(
        closes: tuple[float, ...],
        period: int,
        visible_start: int,
        visible_count: int,
    ) -> tuple[float | None, ...]:
        all_values = calculate_sma_series(closes, period)
        return all_values[visible_start : visible_start + visible_count]

    def _draw_volume(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        series: ChartSeries,
        x_positions: tuple[float, ...],
        display_index: int,
    ) -> None:
        self._draw_grid(painter, chart, horizontal=2, vertical=6)
        maximum = max((bar.volume for bar in bars), default=0.0)
        width = self._bar_body_width(chart, x_positions, len(bars))
        previous_close = series.previous_close
        for bar, x in zip(bars, x_positions):
            height = 0 if maximum == 0 else bar.volume / maximum * chart.height()
            color = self._volume_color(bar, previous_close, series.period)
            painter.fillRect(
                QRectF(x - width / 2, chart.bottom() - height, width, height),
                color,
            )
            previous_close = bar.close
        painter.setPen(QColor("#555555"))
        painter.drawText(
            chart.left() + 6,
            chart.top() + 16,
            self._volume_panel_text(bars[display_index]),
        )

    def _display_bar_index(
        self,
        main_chart: QRectF,
        x_positions: tuple[float, ...],
    ) -> int:
        if not x_positions:
            return 0
        if self._mouse_position is None or not main_chart.contains(self._mouse_position):
            return len(x_positions) - 1
        return min(
            range(len(x_positions)),
            key=lambda item: abs(x_positions[item] - self._mouse_position.x()),
        )

    @classmethod
    def _volume_panel_text(cls, bar: MarketBar) -> str:
        # 分时接口会返回精确成交额；K 线接口未提供时按成交均价近似计算。
        amount = bar.amount
        if amount is None:
            amount = bar.close * bar.volume * 100
        return f"成交量  量：{cls._hands(bar.volume)}手  成交额：{cls._amount(amount)}"

    @staticmethod
    def _volume_color(
        bar: MarketBar,
        previous_close: float | None,
        period: str,
    ) -> QColor:
        if period not in {"intraday", "five_day"}:
            return QColor("#e53935") if bar.close >= bar.open else QColor("#269a49")
        if previous_close is None or bar.close > previous_close:
            return QColor("#e53935")
        if bar.close < previous_close:
            return QColor("#269a49")
        return QColor("#8b8f96")

    def _draw_indicator(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_bars: tuple[MarketBar, ...],
        visible_start: int,
        x_positions: tuple[float, ...],
    ) -> None:
        self._draw_grid(painter, chart, horizontal=3, vertical=6)
        all_closes = tuple(bar.close for bar in all_bars)
        if self.indicator == "macd":
            self._draw_macd(
                painter,
                chart,
                bars,
                all_closes,
                visible_start,
                x_positions,
            )
        elif self.indicator == "kdj":
            self._draw_kdj(
                painter,
                chart,
                bars,
                all_bars,
                all_closes,
                visible_start,
                x_positions,
            )
        else:
            self._draw_rsi(
                painter,
                chart,
                bars,
                all_closes,
                visible_start,
                x_positions,
            )

    def _draw_rsi(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_closes: tuple[float, ...],
        visible_start: int,
        x_positions: tuple[float, ...],
    ) -> None:
        all_values = calculate_rsi_series(all_closes, 6)
        values = all_values[visible_start : visible_start + len(bars)]
        for reference in (30.0, 70.0):
            y = self._value_y(reference, chart, 0, 100)
            painter.setPen(QPen(QColor("#c4c7cc"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        painter.setPen(QPen(QColor("#9c27b0"), 1.1))
        self._draw_series(painter, values, x_positions, chart, 0, 100)
        latest = next((value for value in reversed(values) if value is not None), None)
        painter.setPen(QColor("#555555"))
        painter.drawText(
            chart.left() + 6,
            chart.top() + 16,
            "RSI(6) --" if latest is None else f"RSI(6) {latest:.2f}",
        )

    def _draw_macd(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_closes: tuple[float, ...],
        visible_start: int,
        x_positions: tuple[float, ...],
    ) -> None:
        all_dif, all_dea, all_histogram = calculate_macd_series(all_closes)
        visible_end = visible_start + len(bars)
        dif = all_dif[visible_start:visible_end]
        dea = all_dea[visible_start:visible_end]
        histogram = all_histogram[visible_start:visible_end]
        bound = max((abs(value) for value in (*dif, *dea, *histogram)), default=1.0)
        bound = max(bound, 0.0001)
        zero_y = self._value_y(0, chart, -bound, bound)
        painter.setPen(QPen(QColor("#999999"), 1))
        painter.drawLine(QPointF(chart.left(), zero_y), QPointF(chart.right(), zero_y))
        width = self._bar_body_width(chart, x_positions, len(histogram))
        for x, value in zip(x_positions, histogram):
            y = self._value_y(value, chart, -bound, bound)
            color = QColor("#e53935") if value >= 0 else QColor("#269a49")
            painter.fillRect(
                QRectF(x - width / 2, min(y, zero_y), width, abs(y - zero_y)),
                color,
            )
        painter.setPen(QPen(QColor("#222222"), 1))
        self._draw_series(painter, dif, x_positions, chart, -bound, bound)
        painter.setPen(QPen(QColor("#f39c12"), 1))
        self._draw_series(painter, dea, x_positions, chart, -bound, bound)
        painter.setPen(QColor("#555555"))
        painter.drawText(chart.left() + 6, chart.top() + 16, "MACD(12,26,9)")

    def _draw_kdj(
        self,
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_bars: tuple[MarketBar, ...],
        all_closes: tuple[float, ...],
        visible_start: int,
        x_positions: tuple[float, ...],
    ) -> None:
        all_k, all_d, all_j = calculate_kdj_series(
            tuple(bar.high for bar in all_bars),
            tuple(bar.low for bar in all_bars),
            all_closes,
        )
        visible_end = visible_start + len(bars)
        k = all_k[visible_start:visible_end]
        d = all_d[visible_start:visible_end]
        j = all_j[visible_start:visible_end]
        minimum = min(0.0, *j)
        maximum = max(100.0, *j)
        for values, color in (
            (k, QColor("#222222")),
            (d, QColor("#f39c12")),
            (j, QColor("#ff40d5")),
        ):
            painter.setPen(QPen(color, 1))
            self._draw_series(
                painter, values, x_positions, chart, minimum, maximum
            )
        painter.setPen(QColor("#555555"))
        painter.drawText(chart.left() + 6, chart.top() + 16, "KDJ(9,3,3)")

    def _draw_footer(self, painter: QPainter, data: MarketChartData) -> None:
        label = CHART_PERIOD_LABELS.get(data.period, data.period)
        zoom_hint = (
            ""
            if data.period in {"intraday", "five_day"}
            else " · 滚轮/拖动"
        )
        painter.setPen(QColor("#555555"))
        painter.drawText(
            QRectF(105, self.height() - 32, self._MAIN_CARD_WIDTH - 220, 26),
            Qt.AlignmentFlag.AlignCenter,
            f"上/下图左退右进 · PgUp/PgDn{zoom_hint}    {label}",
        )

    @staticmethod
    def _draw_grid(
        painter: QPainter,
        chart: QRectF,
        horizontal: int,
        vertical: int,
    ) -> None:
        painter.setPen(QPen(QColor("#d9dce1"), 1, Qt.PenStyle.DashLine))
        for index in range(horizontal + 1):
            y = chart.top() + chart.height() * index / horizontal
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        for index in range(vertical + 1):
            x = chart.left() + chart.width() * index / vertical
            painter.drawLine(QPointF(x, chart.top()), QPointF(x, chart.bottom()))
        painter.setPen(QPen(QColor("#b7bbc2"), 1))
        painter.drawRect(chart)

    @classmethod
    def _draw_price_labels(
        cls,
        painter: QPainter,
        chart: QRectF,
        minimum: float,
        maximum: float,
        series: ChartSeries,
    ) -> None:
        if series.period == "intraday" and series.previous_close is not None:
            previous_close = series.previous_close
            percent_range = (maximum / previous_close - 1) * 100
            for index in range(5):
                percent = percent_range * (1 - index / 2)
                price = previous_close * (1 + percent / 100)
                y = chart.top() + chart.height() * index / 4
                color = (
                    QColor("#e53935")
                    if percent > 0
                    else QColor("#269a49") if percent < 0 else QColor("#555555")
                )
                painter.setPen(color)
                painter.drawText(
                    QRectF(chart.left() - 54, y - 9, 50, 18),
                    Qt.AlignmentFlag.AlignRight,
                    f"{price:.3f}",
                )
                painter.drawText(
                    QRectF(chart.right() + 4, y - 9, 50, 18),
                    Qt.AlignmentFlag.AlignLeft,
                    "0.00%" if abs(percent) < 0.0001 else f"{percent:+.2f}%",
                )
            return
        painter.setPen(QColor("#555555"))
        painter.drawText(
            QRectF(chart.left() - 54, chart.top() - 7, 50, 18),
            Qt.AlignmentFlag.AlignRight,
            f"{maximum:.3f}",
        )
        painter.drawText(
            QRectF(chart.left() - 54, chart.bottom() - 10, 50, 18),
            Qt.AlignmentFlag.AlignRight,
            f"{minimum:.3f}",
        )

    @staticmethod
    def _draw_time_labels(
        painter: QPainter,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        period: str,
        x_positions: tuple[float, ...],
    ) -> None:
        if not bars:
            return
        painter.setPen(QColor("#666666"))
        if period == "intraday":
            has_auction = any(bar.timestamp.time() < clock_time(9, 30) for bar in bars)
            normal_left = chart.left() + chart.width() * (
                IntradayChartPanel._AUCTION_WIDTH_RATIO if has_auction else 0
            )
            if has_auction:
                painter.drawText(
                    QRectF(chart.left(), chart.bottom() - 18, 45, 18),
                    Qt.AlignmentFlag.AlignLeft,
                    "09:15",
                )
            painter.drawText(
                QRectF(normal_left - 22, chart.bottom() - 18, 45, 18),
                Qt.AlignmentFlag.AlignCenter,
                "09:30",
            )
            center_x = normal_left + (chart.right() - normal_left) / 2
            painter.drawText(
                QRectF(center_x - 45, chart.bottom() - 18, 90, 18),
                Qt.AlignmentFlag.AlignCenter,
                "11:30/13:00",
            )
            painter.drawText(
                QRectF(chart.right() - 45, chart.bottom() - 18, 45, 18),
                Qt.AlignmentFlag.AlignRight,
                "15:00",
            )
            return
        indices = tuple(dict.fromkeys((0, len(bars) // 2, len(bars) - 1)))
        alignments = {
            1: (Qt.AlignmentFlag.AlignCenter,),
            2: (Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignRight),
            3: (
                Qt.AlignmentFlag.AlignLeft,
                Qt.AlignmentFlag.AlignCenter,
                Qt.AlignmentFlag.AlignRight,
            ),
        }[len(indices)]
        for index, alignment in zip(indices, alignments):
            timestamp = bars[index].timestamp
            label = timestamp.strftime("%H:%M") if len({bar.timestamp.date() for bar in bars}) == 1 else timestamp.strftime("%m-%d")
            x = x_positions[index]
            painter.drawText(QRectF(x - 40, chart.bottom() - 18, 80, 18), alignment, label)

    @classmethod
    def _price_range(
        cls,
        bars: tuple[MarketBar, ...],
        series: ChartSeries,
        all_bars: tuple[MarketBar, ...] | None = None,
        visible_start: int = 0,
    ) -> tuple[float, float]:
        if (
            series.period == "intraday"
            and series.previous_close is not None
            and series.previous_close > 0
        ):
            previous_close = series.previous_close
            maximum_deviation = max(
                max(
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
                / previous_close
                * 100
                for bar in bars
            )
            percent_range = max(2.0, ceil(maximum_deviation * 1.1 / 0.5) * 0.5)
            return (
                previous_close * (1 - percent_range / 100),
                previous_close * (1 + percent_range / 100),
            )
        minimum = min(bar.low for bar in bars)
        maximum = max(bar.high for bar in bars)
        if series.period in {"day", "week", "month"} and all_bars:
            closes = tuple(bar.close for bar in all_bars)
            for period in (5, 10, 20, 60):
                values = cls._visible_moving_average_values(
                    closes, period, visible_start, len(bars)
                )
                visible_values = tuple(value for value in values if value is not None)
                if visible_values:
                    minimum = min(minimum, *visible_values)
                    maximum = max(maximum, *visible_values)
        if series.previous_close is not None:
            minimum = min(minimum, series.previous_close)
            maximum = max(maximum, series.previous_close)
        padding = max((maximum - minimum) * 0.08, maximum * 0.001)
        return minimum - padding, maximum + padding

    @classmethod
    def _bar_x_positions(
        cls,
        chart: QRectF,
        bars: tuple[MarketBar, ...],
        period: str,
    ) -> tuple[float, ...]:
        if not bars:
            return ()
        if period != "intraday":
            width = chart.width() / max(len(bars), 1)
            return tuple(chart.left() + (index + 0.5) * width for index in range(len(bars)))
        has_auction = any(bar.timestamp.time() < clock_time(9, 30) for bar in bars)
        auction_width = cls._AUCTION_WIDTH_RATIO if has_auction else 0.0
        return tuple(
            chart.left()
            + chart.width() * cls._intraday_time_ratio(bar.timestamp, auction_width)
            for bar in bars
        )

    @staticmethod
    def _intraday_time_ratio(timestamp: datetime, auction_width: float) -> float:
        current = timestamp.time()
        if current < clock_time(9, 30):
            seconds = max(
                (timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second)
                - (9 * 3600 + 15 * 60),
                0,
            )
            return min(seconds / 600, 1) * auction_width
        if current <= clock_time(11, 30):
            trading_seconds = (
                timestamp.hour * 3600
                + timestamp.minute * 60
                + timestamp.second
                - (9 * 3600 + 30 * 60)
            )
        elif current < clock_time(13, 0):
            trading_seconds = 2 * 60 * 60
        else:
            trading_seconds = 2 * 60 * 60 + (
                timestamp.hour * 3600
                + timestamp.minute * 60
                + timestamp.second
                - 13 * 3600
            )
        normal_ratio = max(0.0, min(trading_seconds / (4 * 60 * 60), 1.0))
        return auction_width + normal_ratio * (1 - auction_width)

    @staticmethod
    def _bar_body_width(
        chart: QRectF, x_positions: tuple[float, ...], bar_count: int
    ) -> float:
        if len(x_positions) > 1:
            gaps = [
                right - left
                for left, right in zip(x_positions, x_positions[1:])
                if right > left
            ]
            slot = min(gaps) if gaps else chart.width() / max(bar_count, 1)
        else:
            slot = chart.width() / 242
        return max(min(slot * 0.72, 8), 1)

    @staticmethod
    def _draw_series(
        painter: QPainter,
        values: tuple[float | None, ...] | tuple[float, ...] | list[float | None],
        x_positions: tuple[float, ...],
        chart: QRectF,
        minimum: float,
        maximum: float,
    ) -> None:
        painter.save()
        # 折线是开放路径，不能继承工具栏等控件留下的实心画刷；否则 Qt
        # 会自动闭合路径并形成斜三角填充，还可能覆盖其他折线。
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(
            IntradayChartPanel._series_path(
                values, chart, minimum, maximum, x_positions
            )
        )
        valid_points = [
            QPointF(x, IntradayChartPanel._value_y(value, chart, minimum, maximum))
            for x, value in zip(x_positions, values)
            if value is not None
        ]
        if len(valid_points) == 1:
            painter.drawEllipse(valid_points[0], 2.5, 2.5)
        painter.restore()

    @staticmethod
    def _series_path(
        values: tuple[float | None, ...] | tuple[float, ...] | list[float | None],
        chart: QRectF,
        minimum: float,
        maximum: float,
        x_positions: tuple[float, ...] | None = None,
    ) -> QPainterPath:
        path = QPainterPath()
        denominator = max(len(values) - 1, 1)
        drawing = False
        for index, value in enumerate(values):
            if value is None:
                drawing = False
                continue
            x = (
                x_positions[index]
                if x_positions is not None
                else chart.left() + chart.width() * index / denominator
            )
            point = QPointF(
                x,
                IntradayChartPanel._value_y(value, chart, minimum, maximum),
            )
            if drawing:
                path.lineTo(point)
            else:
                path.moveTo(point)
                drawing = True
        return path

    @staticmethod
    def _value_y(value: float, chart: QRectF, minimum: float, maximum: float) -> float:
        if maximum == minimum:
            return chart.center().y()
        return chart.bottom() - (value - minimum) / (maximum - minimum) * chart.height()

    @staticmethod
    def _visible_bars(
        series: ChartSeries,
        show_call_auction: bool = True,
        live_auction_bars: tuple[MarketBar, ...] = (),
        visible_limit: int | None = None,
    ) -> tuple[MarketBar, ...]:
        if series.period in {"intraday", "five_day"}:
            auction_bars = live_auction_bars or series.auction_bars
            if series.period == "intraday" and show_call_auction and auction_bars:
                if not series.bars:
                    return auction_bars
                auction_date = auction_bars[-1].timestamp.date()
                regular_date = series.bars[-1].timestamp.date()
                if auction_date > regular_date:
                    return auction_bars
                if auction_date == regular_date:
                    return (*auction_bars, *series.bars)
            return series.bars
        limit = 180 if visible_limit is None else visible_limit
        return series.bars[-limit:]

    def _visible_range(self, period: str, total_bars: int) -> tuple[int, int]:
        stored = self._zoom_ranges.get(period)
        if stored is None:
            return max(0, total_bars - min(total_bars, 180)), total_bars
        start, end, stored_total = stored
        count = min(end - start, total_bars)
        if end >= stored_total:
            end = total_bars
            start = max(0, end - count)
        else:
            start = min(start, max(total_bars - count, 0))
            end = min(start + count, total_bars)
        self._zoom_ranges[period] = (start, end, total_bars)
        return start, end

    def _apply_zoom_step(
        self,
        period: str,
        total_bars: int,
        zoom_in: bool,
        anchor_ratio: float = 1.0,
    ) -> int:
        start, end = self._visible_range(period, total_bars)
        current = end - start
        if zoom_in:
            updated = max(20, round(current * 0.8))
        else:
            updated = min(total_bars, max(current + 1, round(current * 1.25)))
        updated = min(updated, total_bars)
        ratio = max(0.0, min(anchor_ratio, 1.0))
        anchor_index = start + ratio * max(current - 1, 0)
        new_start = round(anchor_index - ratio * max(updated - 1, 0))
        new_start = max(0, min(new_start, total_bars - updated))
        self._zoom_ranges[period] = (
            new_start,
            new_start + updated,
            total_bars,
        )
        return updated

    def _apply_pan_delta(
        self,
        period: str,
        total_bars: int,
        start: int,
        end: int,
        delta_pixels: float,
        chart_width: float,
    ) -> tuple[int, int]:
        count = end - start
        if count <= 0 or count >= total_bars or chart_width <= 0:
            return start, end
        bar_width = chart_width / count
        bar_shift = round(-delta_pixels / bar_width)
        new_start = max(0, min(start + bar_shift, total_bars - count))
        new_end = new_start + count
        self._zoom_ranges[period] = (new_start, new_end, total_bars)
        return new_start, new_end

    def _draw_hover(
        self,
        painter: QPainter,
        main_chart: QRectF,
        indicator_chart: QRectF,
        bars: tuple[MarketBar, ...],
        all_bars: tuple[MarketBar, ...],
        visible_start: int,
        series: ChartSeries,
        x_positions: tuple[float, ...],
        minimum: float,
        maximum: float,
    ) -> None:
        if self._mouse_position is None or not main_chart.contains(self._mouse_position):
            return
        index = min(
            range(len(x_positions)),
            key=lambda item: abs(x_positions[item] - self._mouse_position.x()),
        )
        bar = bars[index]
        x = x_positions[index]
        y = self._value_y(bar.close, main_chart, minimum, maximum)
        painter.setPen(QPen(QColor("#70757a"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, main_chart.top()), QPointF(x, indicator_chart.bottom()))
        painter.drawLine(QPointF(main_chart.left(), y), QPointF(main_chart.right(), y))
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#4b7b98"), 1.5))
        painter.drawEllipse(QPointF(x, y), 3.5, 3.5)

        lines = self._hover_text_lines(
            series,
            bars,
            index,
            current_rsi=self._data.rsi if self._data is not None else None,
            all_bars=all_bars,
            full_index=visible_start + index,
        )
        tooltip_width = 205.0
        tooltip_height = 106.0
        tooltip_x = x + 12
        if tooltip_x + tooltip_width > main_chart.right():
            tooltip_x = x - tooltip_width - 12
        tooltip_y = max(
            main_chart.top() + 6,
            min(y - tooltip_height / 2, main_chart.bottom() - tooltip_height - 6),
        )
        tooltip = QRectF(tooltip_x, tooltip_y, tooltip_width, tooltip_height)
        # 悬浮信息不绘制底板、阴影或边框，避免遮挡分时走势。
        painter.setPen(QColor("#202124"))
        for line_index, line in enumerate(lines):
            painter.drawText(
                tooltip.left() + 8,
                tooltip.top() + 17 + line_index * 19,
                line,
            )

    @staticmethod
    def _hover_reference_price(
        series: ChartSeries,
        bars: tuple[MarketBar, ...],
        index: int,
        all_bars: tuple[MarketBar, ...] | None = None,
        full_index: int | None = None,
    ) -> float | None:
        if series.period in {"intraday", "five_day"}:
            return series.previous_close
        if all_bars is not None and full_index is not None and full_index > 0:
            return all_bars[full_index - 1].close
        if index > 0:
            return bars[index - 1].close
        return series.previous_close

    @classmethod
    def _hover_text_lines(
        cls,
        series: ChartSeries,
        bars: tuple[MarketBar, ...],
        index: int,
        current_rsi: float | None = None,
        all_bars: tuple[MarketBar, ...] | None = None,
        full_index: int | None = None,
    ) -> tuple[str, str, str, str, str]:
        bar = bars[index]
        calculation_bars = all_bars or bars
        calculation_index = index if full_index is None else full_index
        reference_price = cls._hover_reference_price(
            series,
            bars,
            index,
            all_bars=calculation_bars,
            full_index=calculation_index,
        )
        change_percent = (
            None
            if reference_price is None or reference_price <= 0
            else (bar.close - reference_price) / reference_price * 100
        )
        rsi = calculate_rsi_series(
            tuple(item.close for item in calculation_bars), 6
        )[calculation_index]
        if rsi is None and calculation_index == len(calculation_bars) - 1:
            rsi = current_rsi
        time_format = (
            "%Y-%m-%d %H:%M:%S"
            if series.period
            in {
                "intraday",
                "five_day",
                "1min",
                "5min",
                "15min",
                "30min",
                "60min",
                "120min",
            }
            else "%Y-%m-%d"
        )
        change_text = "--" if change_percent is None else f"{change_percent:+.2f}%"
        rsi_text = "--" if rsi is None else f"{rsi:.2f}"
        amount = bar.amount
        if amount is None:
            amount = bar.close * bar.volume * 100
        return (
            f"时间：{bar.timestamp.strftime(time_format)}",
            f"点位：{bar.close:.3f}",
            f"涨跌：{change_text}",
            f"RSI(6)：{rsi_text}",
            f"成交额：{cls._amount(amount)}",
        )

    def wheelEvent(self, event: QWheelEvent) -> None:
        if (
            self._data is None
            or self._data.series is None
            or self._data.period in {"intraday", "five_day"}
            or not self._data.series.bars
        ):
            super().wheelEvent(event)
            return
        main_chart, _, _ = self._chart_rectangles()
        anchor_ratio = (
            (event.position().x() - main_chart.left()) / main_chart.width()
            if main_chart.contains(event.position())
            else 1.0
        )
        self._apply_zoom_step(
            self._data.period,
            len(self._data.series.bars),
            zoom_in=event.angleDelta().y() > 0,
            anchor_ratio=anchor_ratio,
        )
        self._mouse_position = None
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._close_rect.contains(event.position()):
            self.hide()
            event.accept()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        }:
            self._click_press_position = event.position()
            self._click_press_region = self._click_region(event.position())
            self._click_press_button = event.button()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._data is not None
            and self._data.series is not None
            and self._data.period not in {"intraday", "five_day"}
            and self._chart_rectangles()[0].contains(event.position())
        ):
            total_bars = len(self._data.series.bars)
            start, end = self._visible_range(self._data.period, total_bars)
            if end - start < total_bars:
                self._pan_start_position = event.position()
                self._pan_start_range = (start, end, total_bars)
                self._is_panning = False
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._pan_start_position is not None
            and self._pan_start_range is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta_pixels = event.position().x() - self._pan_start_position.x()
            if abs(delta_pixels) >= 3:
                self._is_panning = True
            if self._is_panning:
                start, end, total_bars = self._pan_start_range
                self._apply_pan_delta(
                    self._data.period,
                    total_bars,
                    start,
                    end,
                    delta_pixels,
                    self._chart_rectangles()[0].width(),
                )
                self._mouse_position = None
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                event.accept()
                return
        self._mouse_position = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pan_start_position is not None:
            was_panning = self._is_panning
            self._pan_start_position = None
            self._pan_start_range = None
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if was_panning:
                self._clear_click_press()
                event.accept()
                return
        if (
            event.button() == self._click_press_button
            and self._click_press_position is not None
            and self._click_press_region is not None
            and self._click_press_region == self._click_region(event.position())
            and (event.position() - self._click_press_position).manhattanLength() <= 6
        ):
            region = self._click_press_region
            step = 1 if event.button() == Qt.MouseButton.RightButton else -1
            self._clear_click_press()
            if region.startswith("period-tab:"):
                self.period_selected.emit(region.partition(":")[2])
                event.accept()
                return
            if region.startswith("indicator-tab:"):
                self.set_indicator(region.partition(":")[2])
                event.accept()
                return
            if region == "refresh":
                self.refresh_requested.emit()
                event.accept()
                return
            if region == "period":
                self._step_click_period(step)
                event.accept()
                return
            if region == "indicator":
                self._step_indicator(step)
                event.accept()
                return
        self._clear_click_press()
        super().mouseReleaseEvent(event)

    def _click_region(self, position: QPointF) -> str | None:
        if self._refresh_rect.contains(position):
            return "refresh"
        for period, rect in self._period_tab_rects.items():
            if rect.contains(position):
                return f"period-tab:{period}"
        for indicator, rect in self._indicator_button_rects.items():
            if rect.contains(position):
                return f"indicator-tab:{indicator}"
        main_chart, volume_chart, indicator_chart = self._chart_rectangles()
        if main_chart.contains(position):
            return "period"
        if volume_chart.contains(position) or indicator_chart.contains(position):
            return "indicator"
        return None

    def _clear_click_press(self) -> None:
        self._click_press_position = None
        self._click_press_region = None
        self._click_press_button = Qt.MouseButton.NoButton

    def _step_click_period(self, step: int) -> None:
        if self._data is None or step == 0:
            return
        current = self._data.period
        if current in self._CLICK_PERIODS:
            index = self._CLICK_PERIODS.index(current)
            updated_index = max(0, min(index + step, len(self._CLICK_PERIODS) - 1))
            period = self._CLICK_PERIODS[updated_index]
        else:
            period = "day" if step > 0 else "intraday"
        if period != current:
            self.period_selected.emit(period)

    def _step_indicator(self, step: int) -> None:
        if step == 0:
            return
        index = self._INDICATORS.index(self.indicator)
        updated_index = max(0, min(index + step, len(self._INDICATORS) - 1))
        if updated_index != index:
            self._indicator_selector.setCurrentIndex(updated_index)

    def leaveEvent(self, event: QEvent) -> None:
        self._mouse_position = None
        self.update()
        super().leaveEvent(event)

    def event(self, event: QEvent) -> bool:
        if (
            event.type() is QEvent.Type.WindowDeactivate
            and self._auto_hide_on_focus_loss
            and self.isVisible()
        ):
            self._focus_loss_timer.start()
        return super().event(event)

    def eventFilter(self, watched, event: QEvent) -> bool:
        if self.isVisible() and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_PageUp:
                self.period_step_requested.emit(-1)
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_PageDown:
                self.period_step_requested.emit(1)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _hide_if_inactive(self) -> None:
        if not self._auto_hide_on_focus_loss:
            return
        if QApplication.activePopupWidget() is not None:
            return
        if QApplication.activeWindow() is not self:
            self.hide()

    def _emit_indicator_changed(self) -> None:
        self.indicator_changed.emit(self.indicator)
        self.update()

    def _emit_period_selected(self) -> None:
        self.period_selected.emit(str(self._period_selector.currentData()))
