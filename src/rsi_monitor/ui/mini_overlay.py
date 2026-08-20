from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QCursor, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget


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
    up_count: int
    down_count: int
    flat_count: int
    change_percent: float | None
    turnover_amount: float | None = None
    previous_same_time_amount: float | None = None


class MiniOverlay(QWidget):
    """最小化后显示的透明、置顶行情摘要。"""

    geometry_changed = Signal()

    def __init__(
        self,
        restore_callback: Callable[[], None],
        hide_callback: Callable[[], None],
        quit_callback: Callable[[], None],
        row_clicked_callback: Callable[[str], None] | None = None,
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
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._restore_callback = restore_callback
        self._hide_callback = hide_callback
        self._quit_callback = quit_callback
        self._row_clicked_callback = row_clicked_callback or (lambda symbol: None)
        self._drag_offset: QPoint | None = None
        self._pressed_row: int | None = None
        self._press_global_position: QPoint | None = None
        self._drag_started = False
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(16)
        self._drag_timer.timeout.connect(self._continue_drag)
        self._rows_layout = QVBoxLayout(self)
        self._rows_layout.setContentsMargins(8, 6, 8, 6)
        self._rows_layout.setSpacing(2)
        self._rows: list[MiniOverlayRow] = []
        self._row_labels: list[tuple[QLabel, QLabel, QLabel, QLabel]] = []
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
        if display_row_count != len(self._row_labels):
            self._rebuild_rows(display_row_count)

        for row, (name, rsi, price, change) in zip(
            rows, self._row_labels[: len(rows)], strict=True
        ):
            name.setText(row.name)
            rsi.setText("--" if row.rsi is None else f"{row.rsi:.2f}")
            price.setText("--" if row.price is None else f"{row.price:.3f}")
            change.setTextFormat(Qt.TextFormat.PlainText)
            change.setMinimumWidth(86)
            change.setText(self._format_change(row.change_percent))
            color = self._color_for_change(row.change_percent)
            for label in (name, rsi, price, change):
                label.setStyleSheet(f"color: {color}; font-size: 12px;")
        if footer is not None:
            name, rsi, price, change = self._row_labels[-1]
            name.setText(footer.name)
            rsi.setText(f"涨{footer.up_count}")
            price.setText(f"跌{footer.down_count}")
            name.setStyleSheet("color: #b0b0b0; font-size: 12px;")
            rsi.setStyleSheet("color: #e53935; font-size: 12px;")
            price.setStyleSheet("color: #31a24c; font-size: 12px;")
            self._set_footer_details(change, footer)
        self._rows = rows
        self.adjustSize()

    def _rebuild_rows(self, row_count: int) -> None:
        self._clear_layout(self._rows_layout)
        self._row_labels.clear()
        for _ in range(row_count):
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            name = QLabel()
            name.setMinimumWidth(160)
            rsi = QLabel()
            rsi.setMinimumWidth(44)
            price = QLabel()
            price.setMinimumWidth(58)
            change = QLabel()
            change.setMinimumWidth(86)
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
        if not self._rows:
            return None
        content_top = self._rows_layout.contentsMargins().top()
        row_height = self._row_labels[0][0].height()
        spacing = self._rows_layout.spacing()
        offset = position.y() - content_top
        if offset < 0:
            return None
        row = offset // (row_height + spacing)
        within_row = offset % (row_height + spacing)
        if row >= len(self._rows) or within_row >= row_height:
            return None
        return row

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
        label.setMinimumWidth(86)
        market_color = cls._color_for_change(footer.change_percent)
        summary = f"平{footer.flat_count}  {cls._format_change(footer.change_percent)}"
        if footer.turnover_amount is None:
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setText(summary)
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
        label.setMinimumWidth(label.sizeHint().width())

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
    def _color_for_change(change_percent: float | None) -> str:
        if change_percent is None or change_percent == 0:
            return "#b0b0b0"
        return "#e53935" if change_percent > 0 else "#31a24c"
