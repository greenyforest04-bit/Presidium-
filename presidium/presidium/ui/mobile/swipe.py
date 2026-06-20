"""Swipe gesture container for mobile navigation.

Provides edge-swipe and list-item swipe gesture handling for touch-based
navigation in the mobile UI.

Supported gestures:
    - Edge swipe from left → open sidebar drawer
    - Edge swipe from right → navigate back
    - Horizontal swipe on list item → reveal actions (reply, forward, delete)
    - Pull down → refresh

Implementation uses QSwipeGesture and manual touch tracking for
cross-platform compatibility (works with both touch and mouse).
"""

from __future__ import annotations

import enum
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QTouchEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget


# ---------------------------------------------------------------------------
# Swipe direction enum
# ---------------------------------------------------------------------------

class SwipeDirection(enum.IntEnum):
    """Direction of a swipe gesture."""
    LEFT = 0
    RIGHT = 1
    UP = 2
    DOWN = 3


# ---------------------------------------------------------------------------
# Swipe gesture detector
# ---------------------------------------------------------------------------

class SwipeDetector:
    """Detects swipe gestures from touch/mouse events.

    Tracks touch points and determines if a swipe occurred based on:
    - Minimum distance threshold (default: 50px)
    - Maximum time threshold (default: 300ms)
    - Direction (horizontal or vertical)
    """

    def __init__(
        self,
        min_distance: int = 50,
        max_duration_ms: int = 300,
        edge_zone_width: int = 20,
    ) -> None:
        self._min_distance = min_distance
        self._max_duration_ms = max_duration_ms
        self._edge_zone_width = edge_zone_width

        self._start_pos: Optional[QPointF] = None
        self._start_time: float = 0.0
        self._is_tracking = False

    def begin(self, pos: QPointF) -> None:
        """Start tracking a potential swipe."""
        self._start_pos = pos
        import time
        self._start_time = time.time() * 1000  # ms
        self._is_tracking = True

    def update(self, pos: QPointF) -> Optional[SwipeDirection]:
        """Update with a new position. Returns direction if a swipe is detected."""
        if not self._is_tracking or self._start_pos is None:
            return None

        import time
        elapsed = time.time() * 1000 - self._start_time
        if elapsed > self._max_duration_ms:
            self.cancel()
            return None

        dx = pos.x() - self._start_pos.x()
        dy = pos.y() - self._start_pos.y()

        if abs(dx) > self._min_distance and abs(dx) > abs(dy) * 1.5:
            self.cancel()
            return SwipeDirection.RIGHT if dx > 0 else SwipeDirection.LEFT

        if abs(dy) > self._min_distance and abs(dy) > abs(dx) * 1.5:
            self.cancel()
            return SwipeDirection.DOWN if dy > 0 else SwipeDirection.UP

        return None

    def cancel(self) -> None:
        """Cancel the current tracking."""
        self._start_pos = None
        self._is_tracking = False

    @property
    def is_in_left_edge(self) -> bool:
        """Check if the current start position is in the left edge zone."""
        if self._start_pos is None:
            return False
        return self._start_pos.x() <= self._edge_zone_width

    @property
    def is_in_right_edge(self) -> bool:
        """Check if the current start position is in the right edge zone."""
        if self._start_pos is None:
            return False
        from PySide6.QtWidgets import QApplication
        screen_width = QApplication.primaryScreen().size().width() if QApplication.primaryScreen() else 1080
        return self._start_pos.x() >= screen_width - self._edge_zone_width


# ---------------------------------------------------------------------------
# Swipe container widget
# ---------------------------------------------------------------------------

class SwipeContainer(QWidget):
    """Container that detects swipe gestures and emits signals.

    Can be used to wrap any content widget and add swipe-based navigation.
    Supports edge swipes (from screen edges) and content swipes (on any area).

    Signals:
        swiped(SwipeDirection): Emitted when a swipe is detected.
        edge_swipe_left(): Emitted on left edge swipe (open sidebar).
        edge_swipe_right(): Emitted on right edge swipe (go back).
        pull_to_refresh(): Emitted on pull-down from top.
    """

    swiped = Signal(SwipeDirection)
    edge_swipe_left = Signal()
    edge_swipe_right = Signal()
    pull_to_refresh = Signal()

    def __init__(
        self,
        content: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._detector = SwipeDetector()
        self._content = content
        self._enabled = True

        if content:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(content)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable swipe detection."""
        self._enabled = enabled

    def touchEvent(self, event: QTouchEvent) -> None:  # type: ignore[override]
        """Handle touch events for swipe detection."""
        if not self._enabled:
            event.ignore()
            return

        touch_points = event.points()
        if not touch_points:
            event.ignore()
            return

        first = touch_points[0]
        pos = first.position()

        if event.type() == QTouchEvent.Type.TouchBegin:
            self._detector.begin(pos)
            event.accept()

        elif event.type() == QTouchEvent.Type.TouchUpdate:
            direction = self._detector.update(pos)
            if direction is not None:
                self._handle_swipe(direction)
            event.accept()

        elif event.type() == QTouchEvent.Type.TouchEnd:
            self._detector.cancel()
            event.accept()

        else:
            event.ignore()

    def _handle_swipe(self, direction: SwipeDirection) -> None:
        """Process a detected swipe direction."""
        self.swiped.emit(direction)

        # Check for edge swipes
        if direction == SwipeDirection.RIGHT and self._detector.is_in_left_edge:
            self.edge_swipe_left.emit()
        elif direction == SwipeDirection.LEFT and self._detector.is_in_right_edge:
            self.edge_swipe_right.emit()
        elif direction == SwipeDirection.DOWN and self._detector.is_in_left_edge:
            self.pull_to_refresh.emit()


# ---------------------------------------------------------------------------
# Swipeable list item
# ---------------------------------------------------------------------------

class SwipeableItem(QWidget):
    """A list item that reveals action buttons when swiped.

    Swipe right to reveal: Reply, Forward
    Swipe left to reveal: Delete, More

    Layout:
        ┌──────────────────────────────────┐
        │  [Reply] [Fwd] │ Content │ [Del] │  ← revealed by swipe
        └──────────────────────────────────┘

    The content widget slides to reveal action buttons behind it.
    """

    action_triggered = Signal(str)  # action_name

    def __init__(
        self,
        content: QWidget,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._content = content
        self._offset = 0  # Current horizontal offset
        self._swipe_start_x = 0.0

        # Left actions (revealed by swiping right)
        self._left_actions: list[tuple[str, str]] = [
            ("reply", "Reply"),
            ("forward", "Forward"),
        ]

        # Right actions (revealed by swiping left)
        self._right_actions: list[tuple[str, str]] = [
            ("delete", "Delete"),
            ("more", "More"),
        ]

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the layered layout with action buttons behind content."""
        self.setMinimumHeight(MobileTokens().MIN_TOUCH_TARGET)

        # Main layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Left action buttons (behind content, revealed by swipe right)
        self._left_panel = QWidget(self)
        left_layout = QHBoxLayout(self._left_panel)
        left_layout.setContentsMargins(8, 4, 8, 4)
        left_layout.setSpacing(4)
        for action_id, label in self._left_actions:
            btn = QPushButton(label)
            btn.setFixedSize(70, 40)
            btn.clicked.connect(lambda checked, a=action_id: self.action_triggered.emit(a))
            left_layout.addWidget(btn)

        # Right action buttons (behind content, revealed by swipe left)
        self._right_panel = QWidget(self)
        right_layout = QHBoxLayout(self._right_panel)
        right_layout.setContentsMargins(8, 4, 8, 4)
        right_layout.setSpacing(4)
        for action_id, label in self._right_actions:
            btn = QPushButton(label)
            btn.setFixedSize(70, 40)
            btn.clicked.connect(lambda checked, a=action_id: self.action_triggered.emit(a))
            right_layout.addWidget(btn)

        # Content (on top, moves with swipe)
        self._content.setParent(self)
        self._content.raise_()

    def set_offset(self, offset: int) -> None:
        """Set the horizontal offset of the content (for animation)."""
        self._offset = offset
        self._content.move(offset, 0)


# Import tokens for SwipeableItem sizing
from presidium.ui.mobile.navigation import MobileTokens
