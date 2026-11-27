"""Width-aware sizing for wrapped brush preview widgets."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)


class _BrushItemDelegate(QStyledItemDelegate):
    def content_rect(self, option, index) -> QRect:
        self.initStyleOption(option, index)
        return option.widget.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, option, option.widget
        )


class BrushListWidget(QListWidget):
    """Keep index widgets at viewport width and give wrapped text its full height.

    QListWidget does not automatically remeasure setItemWidget() children when
    their height-for-width changes. Coalesce layout updates so splitter dragging,
    scrollbar changes and batches of edited labels do not reflow once per label.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(False)
        self.setSpacing(4)
        self._brush_delegate = _BrushItemDelegate(self)
        self.setItemDelegate(self._brush_delegate)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._resize_items)
        self.placeholder = QLabel(self.viewport())
        self.placeholder.setObjectName("listPlaceholder")
        self.placeholder.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def setItemWidget(self, item, widget) -> None:
        previous = self.itemWidget(item)
        if previous is not None:
            previous.removeEventFilter(self)
        super().setItemWidget(item, widget)
        if widget is not None:
            widget.installEventFilter(self)
        self.request_item_layout()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.FontChange,
                            QEvent.Type.StyleChange):
            self.request_item_layout()
        return super().eventFilter(watched, event)

    def request_item_layout(self) -> None:
        """Request one deferred measurement after Qt has settled child layouts."""
        if not self._layout_timer.isActive():
            self._layout_timer.start(0)

    def set_placeholder_text(self, text: str) -> None:
        self.placeholder.setText(text)
        self.placeholder.setGeometry(self.viewport().rect().adjusted(16, 16, -16, -16))
        self.placeholder.setVisible(not any(
            not self.item(index).isHidden() for index in range(self.count())
        ))

    def viewportEvent(self, event) -> bool:
        result = super().viewportEvent(event)
        # The vertical scrollbar resizes the viewport without resizing the list.
        if event.type() == QEvent.Type.Resize and hasattr(self, "_layout_timer"):
            self.placeholder.setGeometry(self.viewport().rect().adjusted(16, 16, -16, -16))
            self.request_item_layout()
        return result

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(
            self, "_layout_timer"
        ):
            self.request_item_layout()
        return result

    def _resize_items(self) -> None:
        width = max(1, self.viewport().width() - 2 * self.spacing())
        changed = False
        for index in range(self.count()):
            item = self.item(index)
            card = self.itemWidget(item)
            if item.isHidden() or card is None:
                continue
            card.ensurePolished()
            card.layout().activate()
            option = QStyleOptionViewItem()
            self.initViewItemOption(option)
            option.rect = QRect(0, 0, width, 10000)
            if item.isSelected():
                option.state |= QStyle.StateFlag.State_Selected
            content = self._brush_delegate.content_rect(option, self.indexFromItem(item))
            # setItemWidget uses the delegate's text rect, not the whole item.
            # Include style borders/padding, otherwise even a 1px border clips text.
            content_width = max(1, content.width())
            height = max(card.minimumSizeHint().height(), card.heightForWidth(content_width))
            size = QSize(width, height + option.rect.height() - content.height())
            if item.sizeHint() != size:
                item.setSizeHint(size)
                changed = True
        if changed:
            self.doItemsLayout()
        # A scrollbar may have appeared/disappeared after measuring the rows.
        if width != max(1, self.viewport().width() - 2 * self.spacing()):
            self.request_item_layout()
