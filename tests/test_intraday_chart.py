from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from rsi_monitor.market_data.base import ChartSeries, MarketBar, MarketQuote
from rsi_monitor.ui.intraday_chart import IntradayChartPanel, MarketChartData


def create_chart_data(period: str = "intraday") -> MarketChartData:
    bars = tuple(
        MarketBar(
            datetime(2026, 8, 12, 9, 30 + index),
            price,
            price + 0.01,
            price - 0.01,
            price + 0.005,
            100 + index * 10,
            (100 + index * 10) * price * 100,
        )
        for index, price in enumerate((2.16, 2.18, 2.17, 2.20, 2.204))
    )
    return MarketChartData(
        symbol="159952",
        name="创业板ETF广发",
        price=2.204,
        rsi=58.13,
        change_percent=1.75,
        timestamp=datetime(2026, 8, 12, 14, 30),
        period=period,
        series=ChartSeries("159952", "创业板ETF广发", period, bars, 2.16),
    )


def send_mouse_click(target: QWidget, global_position: QPoint) -> None:
    local_position = QPointF(10, 10)
    for event_type, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        QApplication.sendEvent(
            target,
            QMouseEvent(
                event_type,
                local_position,
                local_position,
                QPointF(global_position),
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
    QApplication.processEvents()


class ReopeningWidget(QWidget):
    def __init__(self, reopen_callback) -> None:
        super().__init__()
        self._reopen_callback = reopen_callback

    def mouseReleaseEvent(self, event) -> None:
        self._reopen_callback()
        super().mouseReleaseEvent(event)


def test_market_chart_accepts_live_data_and_renders(qtbot) -> None:
    panel = IntradayChartPanel()
    qtbot.addWidget(panel)
    data = create_chart_data()

    panel.set_data(data)
    image = QImage(panel.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    panel.render(image)

    rendered_colors = {
        image.pixelColor(x, y).name()
        for y in range(image.height())
        for x in range(image.width())
    }
    assert panel._data == data
    assert "#4b7b98" in rendered_colors
    assert "#c2a459" in rendered_colors
    assert "#e53935" in rendered_colors
    assert "#269a49" in rendered_colors


def test_set_data_does_not_reuse_zoom_offset_for_another_chart(qtbot) -> None:
    panel = IntradayChartPanel("rsi", quote_panel_collapsed=True)
    qtbot.addWidget(panel)

    panel.set_data(create_chart_data("day"))
    panel._zoom_ranges.update(
        {
            "day": (0, 20, 200),
            "week": (40, 60, 200),
        }
    )

    # A data refresh for the same chart must keep the user's current offset.
    panel.set_data(create_chart_data("day"))
    assert panel._zoom_ranges["day"] == (0, 20, 200)

    # Switching period keeps unrelated same-symbol ranges but starts the new
    # period from its latest page.
    panel.set_data(create_chart_data("week"))
    assert "day" in panel._zoom_ranges
    assert "week" not in panel._zoom_ranges

    # Switching symbol must discard every old offset.
    panel.set_data(replace(create_chart_data("day"), symbol="600000"))
    assert panel._zoom_ranges == {}


def test_open_series_never_inherits_a_solid_fill_brush() -> None:
    image = QImage(101, 101, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setBrush(QColor("#ff0000"))
    painter.setPen(QPen(QColor("#0000ff"), 1))

    IntradayChartPanel._draw_series(
        painter,
        (20.0, 80.0, 20.0),
        (0.0, 50.0, 100.0),
        QRectF(0, 0, 100, 100),
        0.0,
        100.0,
    )
    painter.end()

    assert image.pixelColor(50, 60).name() == "#ffffff"


def test_blue_area_fill_is_only_enabled_for_intraday_trend() -> None:
    bars = (
        MarketBar(datetime(2026, 8, 13, 9, 30), 80, 80, 80, 80, 100, 800_000),
        MarketBar(datetime(2026, 8, 13, 9, 31), 80, 80, 80, 80, 100, 800_000),
    )

    def render(fill_area: bool) -> QImage:
        image = QImage(101, 101, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        IntradayChartPanel()._draw_trend_lines(
            painter,
            QRectF(0, 0, 100, 100),
            bars,
            (0.0, 100.0),
            0.0,
            100.0,
            fill_area=fill_area,
        )
        painter.end()
        return image

    intraday = render(True)
    other_trend = render(False)

    assert intraday.pixelColor(50, 60).name() != "#ffffff"
    assert other_trend.pixelColor(50, 60).name() == "#ffffff"


def test_intraday_volume_color_uses_previous_minute_price() -> None:
    timestamp = datetime(2026, 8, 12, 9, 31)
    bar = MarketBar(timestamp, 2.1, 2.1, 2.1, 2.1, 100)

    assert IntradayChartPanel._volume_color(bar, 2.09, "intraday").name() == "#e53935"
    assert IntradayChartPanel._volume_color(bar, 2.11, "intraday").name() == "#269a49"
    assert IntradayChartPanel._volume_color(bar, 2.10, "intraday").name() == "#8b8f96"


def test_volume_panel_shows_volume_and_exact_or_estimated_amount() -> None:
    timestamp = datetime(2026, 8, 13, 10, 28)
    exact = MarketBar(timestamp, 2.24, 2.24, 2.24, 2.24, 58_500, 13_100_000)
    estimated = MarketBar(timestamp, 2.24, 2.24, 2.24, 2.24, 58_500)

    assert IntradayChartPanel._volume_panel_text(exact) == (
        "成交量  量：5.85万手  成交额：1310.00万"
    )
    assert IntradayChartPanel._volume_panel_text(estimated) == (
        "成交量  量：5.85万手  成交额：1310.40万"
    )


def test_volume_panel_uses_hovered_bar_or_latest_bar() -> None:
    panel = IntradayChartPanel()
    chart = QRectF(100, 40, 300, 200)
    positions = (100.0, 250.0, 400.0)

    assert panel._display_bar_index(chart, positions) == 2

    panel._mouse_position = QPointF(245, 100)

    assert panel._display_bar_index(chart, positions) == 1


def test_page_keys_request_period_changes_and_indicator_is_selectable(qtbot) -> None:
    requested_steps: list[int] = []
    selected_periods: list[str] = []
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    panel.period_step_requested.connect(requested_steps.append)
    panel.period_selected.connect(selected_periods.append)
    panel.show()

    QTest.keyClick(panel, Qt.Key.Key_PageDown)
    QTest.keyClick(panel, Qt.Key.Key_PageUp)
    panel._period_selector.setCurrentIndex(panel._period_selector.findData("15min"))
    panel.set_indicator("macd")

    assert requested_steps == [1, -1]
    assert selected_periods == ["15min"]
    assert panel.indicator == "macd"


def test_chart_stays_open_for_popup_child_owned_by_chart(qtbot) -> None:
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)

    popup = QWidget(panel, Qt.WindowType.Popup)
    popup_child = QWidget(popup)
    popup_child.resize(20, 20)
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(10, 10),
        QPointF(10, 10),
        QPointF(panel.frameGeometry().bottomRight() + QPoint(100, 100)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel._hide_if_clicked_outside(popup_child, event)
    QApplication.processEvents()

    assert panel.isVisible()


def test_page_shortcuts_work_when_closed_period_selector_has_focus(qtbot) -> None:
    requested_steps: list[int] = []
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    panel.period_step_requested.connect(requested_steps.append)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    panel._period_selector.setFocus()
    qtbot.wait(20)

    QTest.keyClick(panel._period_selector, Qt.Key.Key_PageUp)
    QTest.keyClick(panel._period_selector, Qt.Key.Key_PageDown)

    assert requested_steps == [-1, 1]


def test_page_shortcuts_work_when_another_rsi_window_has_focus(qtbot) -> None:
    requested_steps: list[int] = []
    panel = IntradayChartPanel("rsi")
    other = QWidget()
    qtbot.addWidget(panel)
    qtbot.addWidget(other)
    panel.set_auto_hide_on_focus_loss(False)
    panel.period_step_requested.connect(requested_steps.append)
    panel.show()
    other.show()
    other.activateWindow()
    other.setFocus()
    qtbot.wait(20)

    QTest.keyClick(other, Qt.Key.Key_PageDown)
    QTest.keyClick(other, Qt.Key.Key_PageUp)

    assert requested_steps == [1, -1]


def test_main_chart_right_click_advances_and_left_click_returns_period(qtbot) -> None:
    selected_periods: list[str] = []
    panel = IntradayChartPanel("rsi", quote_panel_collapsed=True)
    qtbot.addWidget(panel)
    panel.period_selected.connect(selected_periods.append)
    panel.set_data(create_chart_data("intraday"))
    panel.show()
    qtbot.waitExposed(panel)
    main_chart, _, _ = panel._chart_rectangles()
    position = main_chart.center().toPoint()

    QTest.mouseClick(panel, Qt.MouseButton.RightButton, pos=position)
    panel.set_data(create_chart_data("five_day"))
    QTest.mouseClick(panel, Qt.MouseButton.RightButton, pos=position)
    panel.set_data(create_chart_data("day"))
    QTest.mouseClick(panel, Qt.MouseButton.LeftButton, pos=position)

    assert selected_periods == ["five_day", "day", "five_day"]


def test_lower_chart_clicks_cycle_technical_indicator(qtbot) -> None:
    changed_indicators: list[str] = []
    panel = IntradayChartPanel("rsi", quote_panel_collapsed=True)
    qtbot.addWidget(panel)
    panel.indicator_changed.connect(changed_indicators.append)
    panel.set_data(create_chart_data())
    panel.show()
    qtbot.waitExposed(panel)
    _, volume_chart, indicator_chart = panel._chart_rectangles()

    QTest.mouseClick(
        panel,
        Qt.MouseButton.RightButton,
        pos=volume_chart.center().toPoint(),
    )
    QTest.mouseClick(
        panel,
        Qt.MouseButton.RightButton,
        pos=indicator_chart.center().toPoint(),
    )
    QTest.mouseClick(
        panel,
        Qt.MouseButton.LeftButton,
        pos=indicator_chart.center().toPoint(),
    )

    assert changed_indicators == ["macd", "kdj", "macd"]
    assert panel.indicator == "macd"


def test_opening_auction_can_be_shown_or_hidden() -> None:
    data = create_chart_data()
    assert data.series is not None
    auction_bars = (
        MarketBar(datetime(2026, 8, 12, 9, 15), 2.15, 2.15, 2.15, 2.15, 10),
        MarketBar(datetime(2026, 8, 12, 9, 25), 2.16, 2.16, 2.16, 2.16, 20),
    )

    visible = IntradayChartPanel._visible_bars(
        data.series, True, auction_bars
    )
    hidden = IntradayChartPanel._visible_bars(
        data.series, False, auction_bars
    )

    assert [bar.timestamp.strftime("%H:%M") for bar in visible[:2]] == ["09:15", "09:25"]
    assert hidden == data.series.bars


def test_new_trading_day_auction_does_not_connect_to_previous_day_chart() -> None:
    data = create_chart_data()
    assert data.series is not None
    auction_bars = (
        MarketBar(datetime(2026, 8, 13, 9, 20), 2.21, 2.21, 2.21, 2.21, 10),
    )

    visible = IntradayChartPanel._visible_bars(data.series, True, auction_bars)

    assert visible == auction_bars


def test_intraday_clock_keeps_auction_in_small_left_section() -> None:
    chart = QRectF(0, 0, 1_000, 300)
    bars = tuple(
        MarketBar(timestamp, 2.1, 2.1, 2.1, 2.1, 100)
        for timestamp in (
            datetime(2026, 8, 13, 9, 15),
            datetime(2026, 8, 13, 9, 20),
            datetime(2026, 8, 13, 9, 25),
            datetime(2026, 8, 13, 9, 30),
            datetime(2026, 8, 13, 15, 0),
        )
    )

    positions = IntradayChartPanel._bar_x_positions(chart, bars, "intraday")

    assert positions == (0, 40, 80, 80, 1_000)


def test_single_intraday_volume_bar_stays_narrow_and_price_point_is_visible(qtbot) -> None:
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    timestamp = datetime(2026, 8, 13, 9, 30)
    bar = MarketBar(timestamp, 2.264, 2.264, 2.264, 2.264, 1_000_000)
    panel.set_data(
        MarketChartData(
            symbol="159325",
            name="半导体ETF南方",
            price=2.264,
            rsi=95.98,
            change_percent=1.12,
            timestamp=timestamp,
            period="intraday",
            series=ChartSeries(
                "159325", "半导体ETF南方", "intraday", (bar,), 2.239
            ),
        )
    )
    image = QImage(panel.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    panel.render(image)

    red_pixels = sum(
        image.pixelColor(x, y).name() == "#e53935"
        for y in range(image.height())
        for x in range(image.width())
    )
    colors = {
        image.pixelColor(x, y).name()
        for y in range(image.height())
        for x in range(image.width())
    }

    assert red_pixels < 2_000
    assert "#4b7b98" in colors


def test_day_week_month_charts_render_ma5_ma10_ma20_ma60(qtbot) -> None:
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    bars = tuple(
        MarketBar(
            datetime(2026, 5, 1 + index % 28),
            10 + index * 0.02,
            10.1 + index * 0.02,
            9.9 + index * 0.02,
            10.05 + index * 0.02,
            1_000 + index,
        )
        for index in range(65)
    )

    for period in ("day", "week", "month"):
        panel.set_data(
            MarketChartData(
                "159952",
                "创业板ETF广发",
                bars[-1].close,
                58.13,
                1.75,
                bars[-1].timestamp,
                period,
                ChartSeries("159952", "创业板ETF广发", period, bars, 10),
            )
        )
        image = QImage(panel.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        panel.render(image)
        colors = {
            image.pixelColor(x, y).name()
            for y in range(image.height())
            for x in range(image.width())
        }

        assert {"#222222", "#f39c12", "#2979ff", "#7e57c2"} <= colors


def test_hover_text_contains_time_price_percent_rsi_and_amount(qtbot) -> None:
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    data = create_chart_data()
    assert data.series is not None
    bars = data.series.bars
    lines = panel._hover_text_lines(
        data.series, bars, len(bars) - 1, current_rsi=data.rsi
    )

    assert lines[0] == "时间：2026-08-12 09:34:00"
    assert lines[1] == "点位：2.209"
    assert lines[2].startswith("涨跌：+")
    assert lines[3] == "RSI(6)：58.13"
    assert lines[4] == "成交额：3.09万"

    panel.set_data(data)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    qtbot.wait(20)
    main_chart, _, _ = panel._chart_rectangles()
    position = QPointF(main_chart.center())
    QApplication.sendEvent(
        panel,
        QMouseEvent(
            QEvent.Type.MouseMove,
            position,
            position,
            QPointF(panel.mapToGlobal(position.toPoint())),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )

    assert panel._mouse_position is not None


def test_intraday_hover_change_is_from_same_day_previous_close() -> None:
    timestamp = datetime(2026, 8, 13, 9, 40)
    bars = (MarketBar(timestamp, 100.3, 100.3, 100.3, 100.3, 100),)
    series = ChartSeries("TEST", "测试", "intraday", bars, previous_close=100.0)

    lines = IntradayChartPanel._hover_text_lines(
        series, bars, 0, current_rsi=55.0
    )

    assert lines[2] == "涨跌：+0.30%"


def test_non_intraday_zoom_changes_visible_bar_count() -> None:
    panel = IntradayChartPanel()
    bars = tuple(
        MarketBar(datetime(2026, 1, 1), 1, 1, 1, 1, 1)
        for _ in range(240)
    )
    series = ChartSeries("TEST", "测试", "day", bars)

    zoomed_in = panel._apply_zoom_step("day", len(bars), True)
    visible = panel._visible_bars(series, visible_limit=zoomed_in)
    zoomed_out = panel._apply_zoom_step("day", len(bars), False)

    assert zoomed_in == 144
    assert len(visible) == 144
    assert zoomed_out == 180


def test_zoomed_kline_uses_compact_right_aligned_slots() -> None:
    panel = IntradayChartPanel()
    chart = QRectF(48, 0, 656, 300)
    bars = tuple(
        MarketBar(datetime(2026, 1, 1) + timedelta(days=index), 1, 1, 1, 1, 1)
        for index in range(20)
    )

    positions = panel._bar_x_positions(chart, bars, "day")
    slot = panel._bar_slot_width(chart.width(), len(bars))
    body_width = panel._bar_body_width(chart, positions, len(bars))

    assert slot == panel._BAR_SLOT_MAX
    assert positions[-1] == pytest.approx(chart.right() - slot / 2)
    assert positions[0] == pytest.approx(chart.right() - slot * (len(bars) - 0.5))
    assert all(
        right - left == pytest.approx(slot)
        for left, right in zip(positions, positions[1:])
    )
    assert body_width == pytest.approx(panel._BAR_BODY_MAX)
    assert body_width > 8


def test_normal_kline_count_still_fills_chart_width() -> None:
    panel = IntradayChartPanel()
    chart = QRectF(48, 0, 656, 300)
    bars = tuple(
        MarketBar(datetime(2026, 1, 1) + timedelta(days=index), 1, 1, 1, 1, 1)
        for index in range(180)
    )

    positions = panel._bar_x_positions(chart, bars, "day")
    slot = panel._bar_slot_width(chart.width(), len(bars))

    assert slot < panel._BAR_SLOT_MAX
    assert positions[0] == pytest.approx(chart.left() + slot / 2)
    assert positions[-1] == pytest.approx(chart.right() - slot / 2)


def test_kline_pan_uses_compact_slot_width() -> None:
    panel = IntradayChartPanel()
    chart_width = 656.0
    panel._zoom_ranges["day"] = (100, 120, 240)
    slot = panel._bar_slot_width(chart_width, 20)

    start, end = panel._apply_pan_delta(
        "day",
        240,
        100,
        120,
        -slot * 2,
        chart_width,
    )

    assert (start, end) == (102, 122)


def test_intraday_quote_panel_uses_right_cards() -> None:
    panel = IntradayChartPanel()
    data = create_chart_data()
    quote = MarketQuote(
        "159952",
        2.204,
        data.timestamp,
        name="创业板ETF广发",
        change_percent=1.75,
        previous_close=2.166,
        bid_levels=((2.203, 100),),
        ask_levels=((2.205, 200),),
    )
    panel.set_data(
        MarketChartData(
            **{**data.__dict__, "quote": quote}
        )
    )

    main_chart, _, _ = panel._chart_rectangles()

    assert main_chart.left() == 48
    assert panel._overview_rect().left() == 728
    assert panel._order_book_rect().left() == 728
    assert panel._percent(0.3) == "+0.30%"
    assert panel._shares(1222.89, 79.74) == "15.34亿"


def test_zoomed_kline_keeps_ma5_and_ma60_from_full_history(qtbot) -> None:
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    closes = tuple(float(value) for value in range(1, 101))

    ma5_values = panel._visible_moving_average_values(
        closes, period=5, visible_start=80, visible_count=20
    )
    ma60_values = panel._visible_moving_average_values(
        closes, period=60, visible_start=80, visible_count=20
    )

    assert len(ma5_values) == 20
    assert ma5_values[0] == 79.0
    assert len(ma60_values) == 20
    assert ma60_values[0] == 51.5


def test_zoom_uses_mouse_position_as_anchor() -> None:
    panel = IntradayChartPanel()

    count = panel._apply_zoom_step("day", 240, True, anchor_ratio=0.5)
    start, end = panel._visible_range("day", 240)

    assert count == 144
    assert end < 240
    assert start > 60
    assert (start + end - 1) / 2 == pytest.approx(149.5, abs=1)


def test_zoomed_kline_can_be_dragged_horizontally(qtbot) -> None:
    panel = IntradayChartPanel("rsi")
    qtbot.addWidget(panel)
    selected_periods: list[str] = []
    panel.period_selected.connect(selected_periods.append)
    bars = tuple(
        MarketBar(
            datetime(2026, 1, 1) + timedelta(days=index),
            10,
            11,
            9,
            10,
            100,
        )
        for index in range(240)
    )
    panel.set_data(
        MarketChartData(
            "159952",
            "创业板ETF广发",
            10,
            50,
            0,
            bars[-1].timestamp,
            "day",
            ChartSeries("159952", "创业板ETF广发", "day", bars),
        )
    )
    panel._zoom_ranges["day"] = (120, 180, 240)
    main_chart, _, _ = panel._chart_rectangles()
    start_position = QPointF(main_chart.center())
    end_position = QPointF(start_position.x() + 120, start_position.y())

    QApplication.sendEvent(
        panel,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            start_position,
            start_position,
            QPointF(panel.mapToGlobal(start_position.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        panel,
        QMouseEvent(
            QEvent.Type.MouseMove,
            end_position,
            end_position,
            QPointF(panel.mapToGlobal(end_position.toPoint())),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        panel,
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            end_position,
            end_position,
            QPointF(panel.mapToGlobal(end_position.toPoint())),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )

    start, end = panel._visible_range("day", len(bars))
    assert start < 120
    assert end - start == 60
    assert panel._pan_start_position is None
    assert panel.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert selected_periods == []


def test_kline_pan_requests_more_history_at_left_edge(qtbot) -> None:
    panel = IntradayChartPanel("rsi", quote_panel_collapsed=True)
    qtbot.addWidget(panel)
    bars = tuple(
        MarketBar(
            datetime(2026, 1, 1) + timedelta(days=index),
            10,
            11,
            9,
            10,
            100,
        )
        for index in range(240)
    )
    panel.set_data(
        MarketChartData(
            "159952",
            "创业板ETF广发",
            10,
            50,
            0,
            bars[-1].timestamp,
            "day",
            ChartSeries("159952", "创业板ETF广发", "day", bars),
        )
    )
    panel._zoom_ranges["day"] = (0, 60, len(bars))
    requested: list[str] = []
    panel.history_requested.connect(requested.append)
    panel.show()
    qtbot.waitExposed(panel)

    chart = panel._chart_rectangles()[0]
    start = chart.center().toPoint()
    end = QPoint(start.x() + 120, start.y())
    QTest.mousePress(panel, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(panel, end)
    QTest.mouseRelease(panel, Qt.MouseButton.LeftButton, pos=end)

    assert requested == ["day"]


def test_short_kline_page_keeps_left_edge_available_for_history_loading() -> None:
    panel = IntradayChartPanel("rsi", quote_panel_collapsed=True)

    start, end = panel._visible_range("day", 100)
    assert (start, end) == (0, 100)

    new_start, new_end = panel._apply_pan_delta(
        "day", 100, start, end, 120, 600
    )

    assert (new_start, new_end) == (0, 100)
    assert panel._zoom_ranges["day"] == (0, 100, 100)


def test_intraday_price_range_uses_exact_largest_downside_deviation() -> None:
    previous_close = 100.0
    bars = (
        MarketBar(datetime(2026, 8, 13, 9, 40), 100.0, 101.8, 96.5, 99.0, 100),
    )
    series = ChartSeries(
        "TEST", "测试", "intraday", bars, previous_close=previous_close
    )

    minimum, maximum = IntradayChartPanel._price_range(bars, series)

    assert minimum == pytest.approx(96.5)
    assert maximum == pytest.approx(103.5)


def test_intraday_price_range_uses_recorded_bar_high_after_price_reverses() -> None:
    previous_close = 100.0
    bars = (
        MarketBar(datetime(2026, 8, 13, 9, 40), 100.0, 104.2, 99.0, 101.0, 100),
    )
    series = ChartSeries(
        "TEST", "测试", "intraday", bars, previous_close=previous_close
    )

    minimum, maximum = IntradayChartPanel._price_range(bars, series)

    assert minimum == pytest.approx(95.8)
    assert maximum == pytest.approx(104.2)


def test_five_day_chart_uses_symmetric_zero_axis_and_baseline_line() -> None:
    bars = (
        MarketBar(datetime(2026, 8, 19, 9, 30), 3618.78, 3618.78, 3618.78, 3618.78, 100),
        MarketBar(datetime(2026, 8, 25, 14, 53), 3398.62, 3398.62, 3360.37, 3398.62, 100),
    )
    series = ChartSeries(
        "399006", "创业板指", "five_day", bars, previous_close=3705.56
    )

    minimum, maximum = IntradayChartPanel._price_range(bars, series)

    assert minimum == pytest.approx(3360.37)
    assert maximum == pytest.approx(4050.75)
    assert (minimum + maximum) / 2 == pytest.approx(series.previous_close)


def test_five_day_price_labels_use_common_baseline() -> None:
    class RecordingPainter:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.rectangles: list[QRectF] = []

        def setPen(self, pen) -> None:
            pass

        def drawText(self, *arguments) -> None:
            self.rectangles.append(arguments[0])
            self.texts.append(arguments[-1])

    bars = (
        MarketBar(datetime(2026, 8, 19, 9, 30), 3618.78, 3618.78, 3618.78, 3618.78, 100),
    )
    series = ChartSeries(
        "399006", "创业板指", "five_day", bars, previous_close=3705.56
    )
    painter = RecordingPainter()

    IntradayChartPanel._draw_price_labels(
        painter, QRectF(60, 20, 600, 300), 3360.37, 4050.75, series
    )

    assert painter.texts == [
        "4050.750",
        "+9.32%",
        "3878.155",
        "+4.66%",
        "3705.560",
        "0.00%",
        "3532.965",
        "-4.66%",
        "3360.370",
        "-9.32%",
    ]
    assert all(
        rectangle.right() <= 660
        for rectangle in painter.rectangles[1::2]
    )
    assert all(
        rectangle.left() >= 60
        for rectangle in painter.rectangles[0::2]
    )


def test_five_day_time_axis_labels_each_trading_day() -> None:
    class RecordingPainter:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def setPen(self, pen) -> None:
            pass

        def drawText(self, *arguments) -> None:
            self.texts.append(arguments[-1])

    dates = (19, 20, 21, 24, 25)
    bars = tuple(
        MarketBar(datetime(2026, 8, day, 9, 30), 1, 1, 1, 1, 100)
        for day in dates
    )
    painter = RecordingPainter()

    IntradayChartPanel._draw_time_labels(
        painter,
        QRectF(60, 20, 600, 300),
        bars,
        "five_day",
        (60.0, 180.0, 300.0, 420.0, 540.0),
    )

    assert painter.texts == ["08-19", "08-20", "08-21", "08-24", "08-25"]


def test_quote_panel_can_collapse_to_compact_width_and_expand(qtbot) -> None:
    panel = IntradayChartPanel()
    qtbot.addWidget(panel)
    data = create_chart_data()
    quote = MarketQuote(
        "159952", 2.204, data.timestamp, name="创业板ETF广发", previous_close=2.166
    )
    panel.set_data(MarketChartData(**{**data.__dict__, "quote": quote}))
    changes: list[bool] = []
    panel.quote_panel_collapsed_changed.connect(changes.append)

    assert panel.size().width() == 960
    assert panel.size().height() == 560

    panel.set_quote_panel_collapsed(True)

    assert panel.size().width() == 720
    assert panel._chart_rectangles()[0].left() == 48
    assert changes == [True]

    panel.set_quote_panel_collapsed(False)

    assert panel.size().width() == 960
    assert changes == [True, False]


def test_header_tabs_and_refresh_are_directly_clickable(qtbot) -> None:
    selected_periods: list[str] = []
    refreshed: list[bool] = []
    panel = IntradayChartPanel("rsi", quote_panel_collapsed=True)
    qtbot.addWidget(panel)
    panel.period_selected.connect(selected_periods.append)
    panel.refresh_requested.connect(lambda: refreshed.append(True))
    panel.set_data(create_chart_data())
    panel.show()
    qtbot.waitExposed(panel)
    image = QImage(panel.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    panel.render(image)

    QTest.mouseClick(
        panel,
        Qt.MouseButton.LeftButton,
        pos=panel._period_tab_rects["day"].center().toPoint(),
    )
    QTest.mouseClick(
        panel,
        Qt.MouseButton.LeftButton,
        pos=panel._indicator_button_rects["macd"].center().toPoint(),
    )
    QTest.mouseClick(
        panel,
        Qt.MouseButton.LeftButton,
        pos=panel._refresh_rect.center().toPoint(),
    )

    assert selected_periods == ["day"]
    assert panel.indicator == "macd"
    assert refreshed == [True]
    assert panel.isVisible()


def test_chart_auto_hides_after_losing_focus_and_switch_can_disable_it(qtbot) -> None:
    panel = IntradayChartPanel()
    other = QWidget()
    qtbot.addWidget(panel)
    qtbot.addWidget(other)
    panel.show()
    other.show()
    other.activateWindow()

    QApplication.sendEvent(panel, QEvent(QEvent.Type.WindowDeactivate))
    qtbot.wait(120)

    assert not panel.isVisible()

    panel.set_auto_hide_on_focus_loss(False)
    panel.show()
    QApplication.sendEvent(panel, QEvent(QEvent.Type.WindowDeactivate))
    qtbot.wait(120)

    assert panel.isVisible()


def test_chart_hides_when_application_deactivates(qtbot) -> None:
    panel = IntradayChartPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)

    application = QApplication.instance()
    assert application is not None
    QApplication.sendEvent(application, QEvent(QEvent.Type.ApplicationDeactivate))
    qtbot.wait(20)

    assert not panel.isVisible()


def test_chart_hides_when_application_receives_click_outside_chart(qtbot) -> None:
    panel = IntradayChartPanel()
    outside = QWidget()
    qtbot.addWidget(panel)
    qtbot.addWidget(outside)
    panel.show()
    outside.show()
    qtbot.waitExposed(panel)
    qtbot.waitExposed(outside)

    outside_position = panel.frameGeometry().bottomRight() + QPoint(100, 100)
    send_mouse_click(outside, outside_position)

    assert not panel.isVisible()


@pytest.mark.parametrize("position_name", ("overview", "order_book", "header"))
def test_chart_hides_when_clicking_detail_panel_outside_plot(
    qtbot, position_name: str
) -> None:
    panel = IntradayChartPanel()
    qtbot.addWidget(panel)
    panel.set_data(create_chart_data())
    panel.show()
    qtbot.waitExposed(panel)

    positions = {
        "overview": panel._overview_rect().center().toPoint(),
        "order_book": panel._order_book_rect().center().toPoint(),
        "header": QPoint(100, 20),
    }
    QTest.mouseClick(
        panel,
        Qt.MouseButton.LeftButton,
        pos=positions[position_name],
    )
    qtbot.wait(20)

    assert not panel.isVisible()


def test_chart_stays_open_when_clicking_chart_plot(qtbot) -> None:
    panel = IntradayChartPanel()
    qtbot.addWidget(panel)
    panel.set_data(create_chart_data())
    panel.show()
    qtbot.waitExposed(panel)

    main_chart, _, _ = panel._chart_rectangles()
    QTest.mouseClick(
        panel,
        Qt.MouseButton.LeftButton,
        pos=main_chart.center().toPoint(),
    )
    qtbot.wait(20)

    assert panel.isVisible()


def test_reopening_chart_cancels_pending_outside_hide(qtbot) -> None:
    panel = IntradayChartPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)

    panel._schedule_hide_after_outside_click()
    panel.cancel_pending_outside_click_hide()
    qtbot.wait(20)

    assert panel.isVisible()


def test_chart_hides_when_clicking_related_floating_widget(qtbot) -> None:
    panel = IntradayChartPanel()
    floating_widget = QWidget()
    qtbot.addWidget(panel)
    qtbot.addWidget(floating_widget)
    panel.show()
    floating_widget.show()
    qtbot.waitExposed(panel)
    qtbot.waitExposed(floating_widget)

    floating_position = panel.frameGeometry().bottomRight() + QPoint(100, 100)
    floating_widget.setGeometry(
        floating_position.x(), floating_position.y(), 20, 20
    )
    assert floating_widget.frameGeometry().contains(floating_position)
    assert not panel.frameGeometry().contains(floating_position)
    send_mouse_click(floating_widget, floating_position)

    assert not panel.isVisible()


def test_outside_click_wins_over_release_handler_that_reopens_chart(qtbot) -> None:
    panel = IntradayChartPanel()
    outside = ReopeningWidget(panel.show)
    qtbot.addWidget(panel)
    qtbot.addWidget(outside)
    panel.show()
    outside.show()
    qtbot.waitExposed(panel)
    qtbot.waitExposed(outside)

    outside_position = panel.frameGeometry().bottomRight() + QPoint(100, 100)
    outside.setGeometry(
        outside_position.x(), outside_position.y(), 20, 20
    )
    send_mouse_click(outside, outside_position)

    assert not panel.isVisible()


def test_chart_outside_click_respects_disabled_auto_hide(qtbot) -> None:
    panel = IntradayChartPanel()
    outside = QWidget()
    qtbot.addWidget(panel)
    qtbot.addWidget(outside)
    panel.set_auto_hide_on_focus_loss(False)
    panel.show()
    outside.show()
    qtbot.waitExposed(panel)
    qtbot.waitExposed(outside)

    outside_position = panel.frameGeometry().bottomRight() + QPoint(100, 100)
    send_mouse_click(outside, outside_position)

    assert panel.isVisible()
