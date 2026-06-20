"""Virtualized list widget for Presidium.

Provides an efficient virtual scrolling container for rendering large
lists of items (e.g., chat messages, contact lists) without creating
a QWidget for every row. Only the items visible in the viewport — plus
a small buffer — are instantiated and laid out; the rest exist only as
data in the model. When the user scrolls, widgets are recycled to
represent different data rows, keeping memory usage O(visible) instead
of O(total).

Architecture:
    VirtualListModel
        ├── Holds the full data list (list[Any])
        ├── Tracks per-item estimated heights (variable-height support)
        ├── Computes the visible range given a scroll offset
        └── Emits signals when data changes

    VirtualListWidget(QWidget)
        ├── QScrollArea-based viewport
        ├── Only renders VISIBLE_BUFFER items above/below the fold
        ├── Recycles QWidget instances via a free-list
        ├── Supports variable-height items (message bubbles)
        └── Smooth scroll-to and scroll-to-bottom with QPropertyAnimation

    VirtualListDelegate (ABC)
        ├── create_widget(index, data) → QWidget
        ├── update_widget(widget, index, data) → None
        └── estimate_height(data) → int

Key design decisions:
    - Variable heights are handled by an estimated-height cache that
      is updated as widgets are actually rendered, so the scroll bar
      gradually converges on the true content height.
    - VISIBLE_BUFFER = 5 items above and below the viewport guard
      against visual gaps during fast scrolling.
    - Widget recycling avoids repeated construction/destruction;
      delegates only *update* an existing widget's content.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from PySide6.QtCore import (
    Qt,
    Signal,
    Slot,
    QPoint,
    QRect,
    QPropertyAnimation,
    QEasingCurve,
    QTimer,
)
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QLabel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VISIBLE_BUFFER: int = 5
"""Number of extra items to render above and below the visible viewport."""

DEFAULT_ITEM_HEIGHT: int = 64
"""Fallback height when no estimate is available."""

SCROLL_ANIMATION_DURATION_MS: int = 200
"""Duration for smooth scroll-to animations."""

SCROLL_BAR_STEP: int = 40
"""Single scroll-step in pixels (mouse wheel / keyboard)."""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class VirtualListError(Exception):
    """Base exception for virtual list operations."""


class InvalidIndexError(VirtualListError):
    """Row index is out of bounds."""


class NoDelegateError(VirtualListError):
    """Attempted to render without a delegate installed."""


# ---------------------------------------------------------------------------
# Virtual List Delegate (Abstract Base Class)
# ---------------------------------------------------------------------------

class VirtualListDelegate(abc.ABC):
    """Abstract delegate that knows how to render list items.

    Subclass this to provide custom rendering logic for your data type.
    The virtual list calls *create_widget* the first time it needs a
    visual for a recycled slot and *update_widget* every time the slot
    is reused for a different data row.

    The *estimate_height* method is called before the widget is shown so
    that the model can approximate total content height.  Once the real
    widget is rendered the model records the actual height.
    """

    @abc.abstractmethod
    def create_widget(self, index: int, data: Any, parent: QWidget) -> QWidget:
        """Create a new widget for the given row.

        Args:
            index: Row index in the data model.
            data: The data object for this row.
            parent: Parent widget (the list viewport).

        Returns:
            A fully initialised QWidget for the row.
        """

    @abc.abstractmethod
    def update_widget(self, widget: QWidget, index: int, data: Any) -> None:
        """Update an existing widget to represent a different row.

        This is called when a recycled widget is reused for a new index.

        Args:
            widget: The previously created widget.
            index: New row index.
            data: New data object.
        """

    @abc.abstractmethod
    def estimate_height(self, data: Any) -> int:
        """Return an estimated pixel height for *data*.

        Used by the model to compute scroll extent before the real
        widget has been rendered.  Returning ``DEFAULT_ITEM_HEIGHT``
        is always safe.
        """


# ---------------------------------------------------------------------------
# Virtual List Model
# ---------------------------------------------------------------------------

class VirtualListModel:
    """Data model backing a :class:`VirtualListWidget`.

    Stores the full data list and a height-cache so that the scroll
    area can compute content geometry without instantiating every row.

    Signals are emitted via the owning ``VirtualListWidget`` because
    plain Python objects cannot define Qt signals.
    """

    def __init__(self) -> None:
        self._data: list[Any] = []
        self._heights: list[int] = []
        self._cumulative_offsets: list[int] = []

    # ------------------------------------------------------------------
    # Data management
    # ------------------------------------------------------------------

    def set_data(self, data: list[Any]) -> None:
        """Replace the entire data list and recompute geometry.

        Args:
            data: New list of row data objects.
        """
        self._data = list(data)
        self._heights = [DEFAULT_ITEM_HEIGHT] * len(self._data)
        self._recompute_offsets()
        logger.debug("Model data set: %d items.", len(self._data))

    def append_data(self, item: Any, estimated_height: int = DEFAULT_ITEM_HEIGHT) -> None:
        """Append a single item to the end of the data list.

        Args:
            item: Data object for the new row.
            estimated_height: Estimated pixel height.
        """
        self._data.append(item)
        self._heights.append(estimated_height)
        self._recompute_offsets()

    def insert_data(self, index: int, item: Any, estimated_height: int = DEFAULT_ITEM_HEIGHT) -> None:
        """Insert an item at *index*.

        Args:
            index: Position to insert at.
            item: Data object.
            estimated_height: Estimated pixel height.

        Raises:
            InvalidIndexError: If *index* is out of range.
        """
        if index < 0 or index > len(self._data):
            raise InvalidIndexError(f"Cannot insert at index {index}; list size is {len(self._data)}.")
        self._data.insert(index, item)
        self._heights.insert(index, estimated_height)
        self._recompute_offsets()

    def remove_data(self, index: int) -> Any:
        """Remove and return the item at *index*.

        Args:
            index: Position to remove.

        Returns:
            The removed data object.

        Raises:
            InvalidIndexError: If *index* is out of range.
        """
        if index < 0 or index >= len(self._data):
            raise InvalidIndexError(f"Cannot remove index {index}; list size is {len(self._data)}.")
        item = self._data.pop(index)
        self._heights.pop(index)
        self._recompute_offsets()
        return item

    def update_data(self, index: int, item: Any) -> None:
        """Replace the data object at *index*.

        Args:
            index: Position to update.
            item: New data object.

        Raises:
            InvalidIndexError: If *index* is out of range.
        """
        if index < 0 or index >= len(self._data):
            raise InvalidIndexError(f"Cannot update index {index}; list size is {len(self._data)}.")
        self._data[index] = item

    def get_data(self, index: int) -> Any:
        """Return the data object at *index*.

        Raises:
            InvalidIndexError: If *index* is out of range.
        """
        if index < 0 or index >= len(self._data):
            raise InvalidIndexError(f"Index {index} out of range (size={len(self._data)}).")
        return self._data[index]

    def clear(self) -> None:
        """Remove all data."""
        self._data.clear()
        self._heights.clear()
        self._cumulative_offsets.clear()

    # ------------------------------------------------------------------
    # Height management
    # ------------------------------------------------------------------

    def set_actual_height(self, index: int, height: int) -> None:
        """Record the true rendered height for a row.

        Called by the widget after layout so that subsequent scroll
        calculations are more accurate.

        Args:
            index: Row index.
            height: Actual pixel height.
        """
        if 0 <= index < len(self._heights):
            old = self._heights[index]
            if old != height:
                self._heights[index] = height
                self._recompute_offsets()

    def get_height(self, index: int) -> int:
        """Return the (estimated or actual) height for *index*."""
        if 0 <= index < len(self._heights):
            return self._heights[index]
        return DEFAULT_ITEM_HEIGHT

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    @property
    def item_count(self) -> int:
        """Number of items in the model."""
        return len(self._data)

    @property
    def total_height(self) -> int:
        """Total content height in pixels."""
        if not self._cumulative_offsets:
            return 0
        return self._cumulative_offsets[-1]

    def y_for_index(self, index: int) -> int:
        """Return the y-offset (top edge) of row *index*."""
        if index <= 0:
            return 0
        if index < len(self._cumulative_offsets):
            return self._cumulative_offsets[index - 1]
        return self.total_height

    def index_for_y(self, y: int) -> int:
        """Return the row index that contains pixel offset *y*.

        Uses binary search on the cumulative-offset table.
        """
        if not self._cumulative_offsets or y < 0:
            return 0
        lo, hi = 0, len(self._cumulative_offsets) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self._cumulative_offsets[mid] <= y:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def visible_range(self, viewport_y: int, viewport_height: int) -> tuple[int, int]:
        """Compute the range of row indices that intersect the viewport.

        Includes :data:`VISIBLE_BUFFER` items above and below.

        Args:
            viewport_y: Top of the viewport in content coordinates.
            viewport_height: Height of the viewport in pixels.

        Returns:
            Tuple of (start_index, end_index) inclusive.
        """
        if not self._data:
            return (0, 0)

        first_visible = self.index_for_y(viewport_y)
        last_visible = self.index_for_y(viewport_y + viewport_height)

        start = max(0, first_visible - VISIBLE_BUFFER)
        end = min(self.item_count - 1, last_visible + VISIBLE_BUFFER)
        return (start, end)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _recompute_offsets(self) -> None:
        """Rebuild the cumulative offset table from the height list."""
        offsets: list[int] = []
        acc = 0
        for h in self._heights:
            acc += h
            offsets.append(acc)
        self._cumulative_offsets = offsets


# ---------------------------------------------------------------------------
# Virtual List Widget
# ---------------------------------------------------------------------------

class VirtualListWidget(QWidget):
    """High-performance virtualized list widget.

    Only renders the items visible in the viewport (plus a small buffer)
    and recycles QWidget instances as the user scrolls, keeping memory
    usage proportional to the visible count rather than the total count.

    Supports variable-height rows — message bubbles with different content
    lengths will have their heights tracked after first render so the
    scrollbar gradually converges on the true extent.

    Signals:
        scrolled(int): Emitted with the current scroll position (y offset).
        reached_bottom(): Emitted when the list is scrolled to the very end.
        item_clicked(int): Emitted with the row index when a row is clicked.

    Usage::

        delegate = MyDelegate()
        vl = VirtualListWidget()
        vl.set_delegate(delegate)
        vl.set_data(my_list_of_items)
    """

    scrolled = Signal(int)
    reached_bottom = Signal()
    item_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = VirtualListModel()
        self._delegate: Optional[VirtualListDelegate] = None

        # Widget pool: maps (index → widget) for currently rendered rows
        self._visible_widgets: dict[int, QWidget] = {}
        # Free list of recycled widgets waiting for reuse
        self._free_widgets: list[QWidget] = []

        self._viewport_y: int = 0
        self._viewport_height: int = 0
        self._last_range: tuple[int, int] = (0, 0)

        self._setup_ui()
        logger.debug("VirtualListWidget created.")

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the internal scroll-area based layout."""
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        # The "content" widget that fills the virtual height
        self._content_widget = QWidget()
        self._content_widget.setStyleSheet("background: transparent;")

        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addStretch(0)  # grows to fill total height

        self._scroll_area.setWidget(self._content_widget)

        # Main layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._scroll_area)

        # Scroll bar signals
        vbar = self._scroll_area.verticalScrollBar()
        vbar.setSingleStep(SCROLL_BAR_STEP)
        vbar.valueChanged.connect(self._on_scroll_value_changed)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_delegate(self, delegate: VirtualListDelegate) -> None:
        """Set the delegate used to create and update row widgets.

        Must be called before :meth:`set_data`.

        Args:
            delegate: The delegate instance.
        """
        self._delegate = delegate
        # Discard all rendered widgets — the new delegate creates new ones
        self._recycle_all()
        logger.debug("Delegate set: %s", type(delegate).__name__)

    def set_data(self, data: list[Any]) -> None:
        """Set the list data and refresh the viewport.

        Args:
            data: List of row data objects.
        """
        self._recycle_all()
        self._model.set_data(data)
        self._update_content_height()
        self._render_visible()
        logger.info("Data set: %d items.", len(data))

    @property
    def item_count(self) -> int:
        """Number of items in the list."""
        return self._model.item_count

    def scroll_to(self, index: int, animate: bool = True) -> None:
        """Scroll so that row *index* is visible in the viewport.

        Args:
            index: Target row index.
            animate: If True, use a smooth scrolling animation.

        Raises:
            InvalidIndexError: If *index* is out of range.
        """
        if index < 0 or index >= self._model.item_count:
            raise InvalidIndexError(f"Cannot scroll to index {index}; size is {self._model.item_count}.")

        target_y = self._model.y_for_index(index)
        vbar = self._scroll_area.verticalScrollBar()

        if animate:
            self._animate_scroll(vbar.value(), target_y)
        else:
            vbar.setValue(target_y)

    def scroll_to_bottom(self, animate: bool = True) -> None:
        """Scroll to the bottom of the list.

        Args:
            animate: If True, use smooth scrolling.
        """
        if self._model.item_count == 0:
            return
        target_y = max(0, self._model.total_height - self._viewport_height)
        vbar = self._scroll_area.verticalScrollBar()
        if animate:
            self._animate_scroll(vbar.value(), target_y)
        else:
            vbar.setValue(target_y)

    def scroll_to_top(self, animate: bool = True) -> None:
        """Scroll to the top of the list.

        Args:
            animate: If True, use smooth scrolling.
        """
        vbar = self._scroll_area.verticalScrollBar()
        if animate:
            self._animate_scroll(vbar.value(), 0)
        else:
            vbar.setValue(0)

    def refresh(self) -> None:
        """Force a full re-render of visible items."""
        self._update_content_height()
        self._render_visible()

    def update_item(self, index: int) -> None:
        """Re-render a single item if it is currently visible.

        Args:
            index: Row index to update.
        """
        if index in self._visible_widgets and self._delegate is not None:
            data = self._model.get_data(index)
            widget = self._visible_widgets[index]
            self._delegate.update_widget(widget, index, data)

    # ------------------------------------------------------------------
    # Event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        """Handle viewport resize by re-rendering."""
        super().resizeEvent(event)
        self._viewport_height = self._scroll_area.viewport().height()
        self._update_content_height()
        self._render_visible()

    # ------------------------------------------------------------------
    # Scroll handling
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_scroll_value_changed(self, value: int) -> None:
        """React to scroll position changes."""
        self._viewport_y = value
        self.scrolled.emit(value)
        self._render_visible()

        # Detect bottom
        vbar = self._scroll_area.verticalScrollBar()
        if vbar.maximum() <= value + 2:
            self.reached_bottom.emit()

    def _animate_scroll(self, start_y: int, end_y: int) -> None:
        """Smooth-scroll from *start_y* to *end_y*.

        Args:
            start_y: Starting scroll position.
            end_y: Target scroll position.
        """
        vbar = self._scroll_area.verticalScrollBar()
        self._scroll_anim = QPropertyAnimation(vbar, b"value")
        self._scroll_anim.setDuration(SCROLL_ANIMATION_DURATION_MS)
        self._scroll_anim.setStartValue(start_y)
        self._scroll_anim.setEndValue(end_y)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._scroll_anim.start()

    # ------------------------------------------------------------------
    # Rendering / recycling
    # ------------------------------------------------------------------

    def _update_content_height(self) -> None:
        """Set the content widget height to match the model's total height."""
        total = self._model.total_height
        min_height = max(total, self._scroll_area.viewport().height())
        self._content_widget.setFixedHeight(min_height)

    def _render_visible(self) -> None:
        """Create / recycle widgets for the visible range."""
        if self._delegate is None:
            raise NoDelegateError("No delegate set. Call set_delegate() first.")

        vp_height = self._scroll_area.viewport().height()
        if vp_height <= 0:
            vp_height = self.height()
        self._viewport_height = vp_height

        start, end = self._model.visible_range(self._viewport_y, vp_height)
        new_range = (start, end)

        # Nothing changed — skip
        if new_range == self._last_range and self._visible_widgets:
            return
        self._last_range = new_range

        # Determine which indices are no longer visible
        to_recycle = [idx for idx in self._visible_widgets if idx < start or idx > end]

        # Recycle invisible widgets
        for idx in to_recycle:
            widget = self._visible_widgets.pop(idx)
            widget.hide()
            self._free_widgets.append(widget)

        # Render visible items
        for idx in range(start, end + 1):
            if idx >= self._model.item_count:
                break
            data = self._model.get_data(idx)
            y_pos = self._model.y_for_index(idx)
            est_height = self._model.get_height(idx)

            if idx in self._visible_widgets:
                # Update existing widget
                widget = self._visible_widgets[idx]
                self._delegate.update_widget(widget, idx, data)
            else:
                # Get a widget (recycled or new)
                widget = self._acquire_widget(idx, data)

            # Position the widget
            widget.move(0, y_pos)
            widget.setFixedWidth(self._scroll_area.viewport().width())
            widget.show()

            # Record actual height after layout
            QTimer.singleShot(0, lambda i=idx, w=widget: self._record_actual_height(i, w))

    def _acquire_widget(self, index: int, data: Any) -> QWidget:
        """Obtain a widget — from the free-list or freshly created.

        Args:
            index: Row index.
            data: Row data.

        Returns:
            A widget ready for display.
        """
        assert self._delegate is not None  # guaranteed by _render_visible

        if self._free_widgets:
            widget = self._free_widgets.pop()
            self._delegate.update_widget(widget, index, data)
        else:
            widget = self._delegate.create_widget(index, data, self._content_widget)

        widget.setParent(self._content_widget)
        self._visible_widgets[index] = widget
        return widget

    def _record_actual_height(self, index: int, widget: QWidget) -> None:
        """Record the real rendered height of a widget back to the model.

        This is called asynchronously so that Qt has a chance to lay out
        the widget before we measure it.

        Args:
            index: Row index.
            widget: The rendered widget.
        """
        actual = widget.sizeHint().height() if widget.sizeHint().height() > 0 else widget.height()
        if actual > 0:
            old = self._model.get_height(index)
            if old != actual:
                self._model.set_actual_height(index, actual)
                # Content height may have changed
                self._update_content_height()

    def _recycle_all(self) -> None:
        """Hide and pool all currently visible widgets."""
        for idx, widget in list(self._visible_widgets.items()):
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self._visible_widgets.clear()
        for widget in self._free_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._free_widgets.clear()
        self._last_range = (0, 0)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def widget_for_index(self, index: int) -> Optional[QWidget]:
        """Return the QWidget for *index* if it is currently rendered, else None."""
        return self._visible_widgets.get(index)

    @property
    def model(self) -> VirtualListModel:
        """Access the underlying data model."""
        return self._model
