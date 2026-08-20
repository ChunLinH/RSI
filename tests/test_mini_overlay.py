from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from rsi_monitor.ui.mini_overlay import MiniOverlay, MiniOverlayFooter, MiniOverlayRow


def test_overlay_shows_name_rsi_price_and_change_percent(qtbot) -> None:
    overlay = MiniOverlay(lambda: None, lambda: None, lambda: None)
    qtbot.addWidget(overlay)

    overlay.set_rows([MiniOverlayRow("159952", "创业板ETF广发", 58.13, 2.204, 1.75)])

    assert [label.text() for label in overlay.findChildren(QLabel)] == [
        "创业板ETF广发",
        "58.13",
        "2.204",
        "+1.75%",
    ]
    labels = overlay.findChildren(QLabel)
    assert all("font-size: 12px" in label.styleSheet() for label in labels)
    assert all("font-weight" not in label.styleSheet() for label in labels)

    overlay.set_rows([MiniOverlayRow("159952", "创业板ETF广发", 58.14, 2.205, 1.76)])

    assert overlay.findChildren(QLabel) == labels
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_entire_overlay_surface_can_be_dragged(qtbot) -> None:
    overlay = MiniOverlay(lambda: None, lambda: None, lambda: None)
    qtbot.addWidget(overlay)
    overlay.set_rows(
        [
            MiniOverlayRow("159952", "创业板ETF广发", 58.13, 2.204, 1.75),
            MiniOverlayRow("159142", "科创创业ETF", 54.24, 1.261, 2.10),
            MiniOverlayRow("159325", "半导体ETF南方", 63.44, 2.250, 2.09),
        ]
    )
    overlay.move(200, 200)
    overlay.show()
    qtbot.waitExposed(overlay)
    surface = overlay._drag_surface
    assert surface.geometry() == overlay.rect()
    assert surface.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert "rgba(0, 0, 0, 0.01)" in surface.styleSheet()
    assert all(
        label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for labels in overlay._row_labels
        for label in labels
    )
    original_position = overlay.pos()
    drag_distance = QPoint(80, 50)
    press_position = QPoint(surface.width() - 5, surface.height() - 5)
    start_position = surface.mapToGlobal(press_position)

    QCursor.setPos(start_position)
    QTest.qWait(50)
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=press_position)
    QCursor.setPos(start_position + drag_distance)
    QTest.qWait(100)
    QTest.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=press_position)

    actual_distance = overlay.pos() - original_position
    # Windows 的逻辑像素换算会让合成光标产生少量取整误差。
    assert actual_distance.x() >= 20
    assert actual_distance.y() >= 20


def test_clicking_a_row_reports_its_symbol(qtbot) -> None:
    clicked_symbols: list[str] = []
    overlay = MiniOverlay(
        lambda: None,
        lambda: None,
        lambda: None,
        clicked_symbols.append,
    )
    qtbot.addWidget(overlay)
    overlay.set_rows(
        [
            MiniOverlayRow("159952", "创业板ETF广发", 58.13, 2.204, 1.75),
            MiniOverlayRow("159142", "科创创业ETF", 54.24, 1.261, 2.10),
        ]
    )
    overlay.show()
    qtbot.waitExposed(overlay)
    first_row_height = overlay._row_labels[0][0].height()
    second_row_position = QPoint(20, 6 + first_row_height + 2 + first_row_height // 2)

    QTest.mouseClick(overlay._drag_surface, Qt.MouseButton.LeftButton, pos=second_row_position)

    assert clicked_symbols == ["159142"]


def test_overlay_shows_fixed_market_breadth_footer(qtbot) -> None:
    clicked_symbols: list[str] = []
    overlay = MiniOverlay(
        lambda: None,
        lambda: None,
        lambda: None,
        clicked_symbols.append,
    )
    qtbot.addWidget(overlay)
    overlay.set_rows(
        [MiniOverlayRow("159952", "创业板ETF广发", 58.13, 2.204, 1.75)],
        MiniOverlayFooter(
            "全A",
            500,
            4650,
            60,
            -2.23,
            turnover_amount=500_000_000_000,
            previous_same_time_amount=300_000_000_000,
        ),
    )
    overlay.show()
    qtbot.waitExposed(overlay)

    labels = overlay.findChildren(QLabel)
    assert [label.text() for label in labels[:7]] == [
        "创业板ETF广发",
        "58.13",
        "2.204",
        "+1.75%",
        "全A",
        "涨500",
        "跌4650",
    ]
    footer_details = overlay._row_labels[-1][3]
    assert "平60  -2.23%" in footer_details.text()
    assert "额5000亿" in footer_details.text()
    assert "较上日+2000亿" in footer_details.text()
    assert footer_details.width() >= footer_details.sizeHint().width()
    footer_name, up_count, down_count, _ = overlay._row_labels[-1]
    assert "#b0b0b0" in footer_name.styleSheet()
    assert "#e53935" in up_count.styleSheet()
    assert "#31a24c" in down_count.styleSheet()

    first_row_height = overlay._row_labels[0][0].height()
    footer_position = QPoint(20, 6 + first_row_height + 2 + first_row_height // 2)

    QTest.mouseClick(overlay._drag_surface, Qt.MouseButton.LeftButton, pos=footer_position)

    assert clicked_symbols == []
