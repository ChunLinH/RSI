from collections.abc import Callable
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QCursor,
    QGuiApplication,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


SORT_MODE_RSI = "rsi"
SORT_MODE_CHANGE = "change_percent"
SORT_MODES = (SORT_MODE_RSI, SORT_MODE_CHANGE)


@dataclass(frozen=True)
class MiniOverlayRow:
    symbol: str
    name: str
    rsi: float | None
    price: float | None
    change_percent: float | None


@dataclass(frozen=True)
class MiniOverlayFooter:
    name: str
    up_count: int | None
    down_count: int | None
    flat_count: int | None
    change_percent: float | None
    turnover_amount: float | None = None
    previous_same_time_amount: float | None = None


class MiniOverlay(QWidget):
    """最小化后显示的透明、置顶行情摘要。"""

    geometry_changed = Signal()
    sort_mode_changed = Signal(str)

    def __init__(
        self,
        restore_callback: Callable[[], None],
        hide_callback: Callable[[], None],
        quit_callback: Callable[[], None],
        row_clicked_callback: Callable[[str], None] | None = None,
        sort_mode: str = SORT_MODE_RSI,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setWindowTitle("RSI 行情摘要")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("miniOverlay")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet(
            """
            #miniOverlay {
                background-color: transparent;
                border: none;
            }
            QLabel#overlayTitle {
                color: #b0b0b0;
                font-size: 13px;
                font-weight: 600;
            }
            QToolButton#sortButton {
                color: #b0b0b0;
                background-color: transparent;
                border: 1px solid rgba(176, 176, 176, 0.5);
                border-radius: 7px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QToolButton#sortButton:hover {
                color: #31a24c;
                border-color: #31a24c;
            }
            QToolButton#sortButton:pressed {
                color: #31a24c;
            }
            QMenu#sortMenu {
                padding: 4px;
            }
            QMenu#sortMenu::item {
                padding: 7px 22px 7px 10px;
                border-radius: 5px;
            }
            QFrame#overlayDivider {
                background-color: rgba(176, 176, 176, 0.35);
                border: none;
                max-height: 1px;
            }
            """
        )
        self._restore_callback = restore_callback
        self._hide_callback = hide_callback
        self._quit_callback = quit_callback
        self._row_clicked_callback = row_clicked_callback or (lambda symbol: None)
        self._sort_mode = SORT_MODE_RSI
        self._drag_offset: QPoint | None = None
        self._pressed_row: int | None = None
        self._press_global_position: QPoint | None = None
        self._drag_started = False
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(16)
        self._drag_timer.timeout.connect(self._continue_drag)
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(12, 10, 12, 10)
        self._root_layout.setSpacing(7)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self._title_label = QLabel(self)
        self._title_label.setObjectName("overlayTitle")
        self._title_label.setTextFormat(Qt.TextFormat.RichText)
        self._title_label.setText(
            '<span style="color:#73cda9">●</span>&nbsp;&nbsp;RSI 盯盘'
        )
        self._title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        self._sort_button = QToolButton(self)
        self._sort_button.setObjectName("sortButton")
        self._sort_button.setToolTip("选择悬浮窗排序方式")
        self._sort_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._sort_button.setFixedHeight(27)
        self._sort_button.setMinimumWidth(112)
        self._sort_menu = QMenu(self)
        self._sort_menu.setObjectName("sortMenu")
        self._sort_button.setMenu(self._sort_menu)
        self._sort_actions = {}
        for mode, label in (
            (SORT_MODE_RSI, "RSI(6) 从高到低"),
            (SORT_MODE_CHANGE, "涨跌% 从高到低"),
        ):
            action = self._sort_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, selected_mode=mode: self.set_sort_mode(
                    selected_mode, emit=True
                )
            )
            self._sort_actions[mode] = action
        header_layout.addWidget(self._sort_button)
        self._root_layout.addLayout(header_layout)
        self._header_divider = QFrame(self)
        self._header_divider.setObjectName("overlayDivider")
        self._header_divider.setFrameShape(QFrame.Shape.HLine)
        self._header_divider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._root_layout.addWidget(self._header_divider)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._root_layout.addLayout(self._rows_layout)
        self._rows: list[MiniOverlayRow] = []
        self._row_labels: list[tuple[QLabel, QLabel, QLabel, QLabel]] = []
        self._footer_present = False
        self.set_sort_mode(sort_mode)
        # 透明拖动层放在文字下方，专门接收文字间隙的鼠标事件。
        # 文字标签本身鼠标穿透到这里，因此整块悬浮窗都能拖动。
        self._drag_surface = QWidget(self)
        self._drag_surface.setStyleSheet("background-color: rgba(0, 0, 0, 0.01);")
        self._drag_surface.setCursor(Qt.CursorShape.ArrowCursor)
        self._drag_surface.installEventFilter(self)

    def set_rows(
        self, rows: list[MiniOverlayRow], footer: MiniOverlayFooter | None = None
    ) -> None:
        display_row_count = len(rows) + (1 if footer is not None else 0)
        footer_present = footer is not None
        if (
            display_row_count != len(self._row_labels)
            or footer_present != self._footer_present
        ):
            self._rebuild_rows(display_row_count, footer_present)

        for row, (name, rsi, price, change) in zip(
            rows, self._row_labels[: len(rows)], strict=True
        ):
            name.setText(row.name)
            rsi.setText("--" if row.rsi is None else f"{row.rsi:.2f}")
            price.setText("--" if row.price is None else f"{row.price:.3f}")
            change.setTextFormat(Qt.TextFormat.PlainText)
            change.setFixedWidth(92)
            change.setText(self._format_change(row.change_percent))
            color = self._color_for_change(row.change_percent)
            for label in (name, rsi, price, change):
                label.setStyleSheet(f"color: {color}; font-size: 12px;")
        if footer is not None:
            name, rsi, price, change = self._row_labels[-1]
            name.setText(footer.name)
            rsi.setText(f"涨{self._format_count(footer.up_count)}")
            price.setText(f"跌{self._format_count(footer.down_count)}")
            name.setStyleSheet("color: #aeb8c5; font-size: 12px;")
            rsi.setStyleSheet("color: #ff6b70; font-size: 12px;")
            price.setStyleSheet("color: #5dcc8a; font-size: 12px;")
            self._set_footer_details(change, footer)
        self._rows = rows
        self.adjustSize()

    def set_sort_mode(self, mode: str, *, emit: bool = False) -> None:
        if mode not in SORT_MODES:
            mode = SORT_MODE_RSI
        self._sort_mode = mode
        label = "RSI(6)" if mode == SORT_MODE_RSI else "涨跌%"
        self._sort_button.setText(f"↕ {label}")
        for action_mode, action in self._sort_actions.items():
            action.setChecked(action_mode == mode)
        if emit:
            self.sort_mode_changed.emit(mode)

    @property
    def sort_mode(self) -> str:
        return self._sort_mode

    def _rebuild_rows(self, row_count: int, footer_present: bool) -> None:
        self._clear_layout(self._rows_layout)
        self._row_labels.clear()
        self._footer_present = footer_present
        for row_index in range(row_count):
            if footer_present and row_index == row_count - 1:
                separator = QFrame(self)
                separator.setObjectName("overlayDivider")
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
                separator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._rows_layout.addWidget(separator)
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            name = QLabel()
            name.setFixedWidth(164)
            rsi = QLabel()
            rsi.setFixedWidth(55)
            price = QLabel()
            price.setFixedWidth(72)
            change = QLabel()
            change.setFixedWidth(92)
            name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for label in (rsi, price, change):
                label.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            for label in (name, rsi, price, change):
                # 标签视觉上位于底层拖动区上方，但鼠标事件会穿透至拖动区。
                # 因此文字、空白和两行之间的缝隙都能拖动窗口。
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                layout.addWidget(label)
            layout.addStretch()
            self._rows_layout.addLayout(layout)
            self._row_labels.append((name, rsi, price, change))
        self._drag_surface.lower()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._drag_surface and isinstance(event, QMouseEvent):
            handlers = {
                QEvent.Type.MouseButtonPress: self.mousePressEvent,
                QEvent.Type.MouseMove: self.mouseMoveEvent,
                QEvent.Type.MouseButtonRelease: self.mouseReleaseEvent,
                QEvent.Type.MouseButtonDblClick: self.mouseDoubleClickEvent,
            }
            handler = handlers.get(event.type())
            if handler is not None:
                handler(event)
                return event.isAccepted()
        if watched is self._drag_surface and isinstance(event, QContextMenuEvent):
            self.contextMenuEvent(event)
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._drag_surface.setGeometry(self.rect())
        self._drag_surface.lower()
        self.geometry_changed.emit()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.geometry_changed.emit()

    @classmethod
    def _clear_layout(cls, layout: QVBoxLayout | QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif child_layout := item.layout():
                cls._clear_layout(child_layout)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # 以系统光标坐标作为拖动起点，避免透明置顶窗口在 Windows
            # 缩放比例下出现事件坐标与真实光标坐标的细小偏移。
            global_position = QCursor.pos()
            self._drag_offset = global_position - self.frameGeometry().topLeft()
            self._press_global_position = global_position
            self._pressed_row = self._row_at(event.position().toPoint())
            self._drag_started = False
            self._drag_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._continue_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_timer.stop()
        clicked_row = self._pressed_row
        should_open_row = not self._drag_started and clicked_row is not None
        self._drag_offset = None
        self._pressed_row = None
        self._press_global_position = None
        self._drag_started = False
        if should_open_row and clicked_row < len(self._rows):
            self._row_clicked_callback(self._rows[clicked_row].symbol)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _continue_drag(self) -> None:
        if self._drag_offset is None:
            self._drag_timer.stop()
            return
        if not QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self._drag_timer.stop()
            self._drag_offset = None
            return
        cursor_position = QCursor.pos()
        if (
            self._press_global_position is not None
            and (cursor_position - self._press_global_position).manhattanLength() >= 6
        ):
            self._drag_started = True
        if self._drag_started:
            self.move(cursor_position - self._drag_offset)

    def _row_at(self, position: QPoint) -> int | None:
        for index, (name, _rsi, _price, _change) in enumerate(self._row_labels):
            if name.geometry().contains(position):
                return index
        return None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._restore_callback()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self._show_context_menu(event.globalPos())

    def _show_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("恢复主窗口", self._restore_callback)
        menu.addAction("隐藏到托盘", self._hide_callback)
        menu.addSeparator()
        menu.addAction("退出程序", self._quit_callback)
        menu.exec(position)

    @staticmethod
    def _format_change(change_percent: float | None) -> str:
        return "--" if change_percent is None else f"{change_percent:+.2f}%"

    @classmethod
    def _set_footer_details(cls, label: QLabel, footer: MiniOverlayFooter) -> None:
        label.setMinimumWidth(92)
        market_color = cls._color_for_change(footer.change_percent)
        summary = (
            f"平{cls._format_count(footer.flat_count)}  "
            f"{cls._format_change(footer.change_percent)}"
        )
        if footer.turnover_amount is None:
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setText(f"{summary}  额--")
            label.setStyleSheet(f"color: {market_color}; font-size: 12px;")
            return
        difference = (
            None
            if footer.previous_same_time_amount is None
            else footer.turnover_amount - footer.previous_same_time_amount
        )
        difference_color = cls._color_for_change(difference)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(
            f'<span style="color:{market_color}">{summary}</span>&nbsp;&nbsp;'
            f'<span style="color:#b0b0b0">额{cls._format_amount(footer.turnover_amount)}</span>'
            f'&nbsp;&nbsp;<span style="color:{difference_color}">'
            f'较上日{cls._format_amount_difference(difference)}</span>'
        )
        label.setStyleSheet("font-size: 12px;")
        label.setFixedWidth(max(92, label.sizeHint().width()))

    @staticmethod
    def _format_amount(amount: float) -> str:
        if amount >= 1_000_000_000_000:
            return f"{amount / 1_000_000_000_000:.2f}万亿"
        return f"{amount / 100_000_000:.0f}亿"

    @staticmethod
    def _format_amount_difference(difference: float | None) -> str:
        if difference is None:
            return "--"
        return f"{difference / 100_000_000:+.0f}亿"

    @staticmethod
    def _format_count(count: int | None) -> str:
        return "--" if count is None else str(count)

    @staticmethod
    def _color_for_change(change_percent: float | None) -> str:
        if change_percent is None or change_percent == 0:
            return "#b0b0b0"
        return "#e53935" if change_percent > 0 else "#31a24c"
