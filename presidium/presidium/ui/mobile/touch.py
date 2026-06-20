"""Touch optimization mixin and utilities for mobile UI components.

Provides a mixin class that applies mobile-friendly sizing and spacing
to any QWidget, ensuring all interactive elements meet minimum touch
target size requirements.

Also includes:
    - Responsive layout helper that switches between compact and expanded
    - Screen size detection utilities
    - Orientation change handler
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from presidium.ui.mobile.navigation import MobileTokens


# ---------------------------------------------------------------------------
# Screen utilities
# ---------------------------------------------------------------------------

class ScreenInfo:
    """Utility class for screen size and density information."""

    @staticmethod
    def is_mobile() -> bool:
        """Check if the current screen is considered mobile-sized.

        Returns True if the shortest screen dimension is less than 600dp.
        """
        app = QApplication.instance()
        if app is None:
            return False
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        size = screen.availableSize()
        return min(size.width(), size.height()) < 600

    @staticmethod
    def screen_size() -> QSize:
        """Get the primary screen's available size."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return QSize(1080, 1920)
        return screen.availableSize()

    @staticmethod
    def dp_to_px(dp: int) -> int:
        """Convert density-independent pixels to physical pixels.

        Uses the screen's device pixel ratio for scaling.
        For simplicity, we assume a 2x DPR for mobile and 1x for desktop.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return dp
        dpr = screen.devicePixelRatio()
        return int(dp * dpr)

    @staticmethod
    def is_landscape() -> bool:
        """Check if the screen is in landscape orientation."""
        size = ScreenInfo.screen_size()
        return size.width() > size.height()


# ---------------------------------------------------------------------------
# Touch optimization mixin
# ---------------------------------------------------------------------------

class TouchOptimizedMixin:
    """Mixin that applies mobile-friendly touch target sizing.

    When mixed into a QWidget subclass, it ensures:
    - All child buttons have minimum 48x48 px touch targets
    - Padding follows mobile spacing guidelines
    - Font sizes are scaled for mobile readability

    Usage:
        class MyWidget(QWidget, TouchOptimizedMixin):
            def __init__(self):
                super().__init__()
                self.apply_touch_optimization()
    """

    def apply_touch_optimization(self) -> None:
        """Apply touch-optimized sizing to this widget and its children.

        Should be called after the widget's UI is fully constructed.
        """
        if not isinstance(self, QWidget):
            return

        tokens = MobileTokens()

        # Enforce minimum size for interactive elements
        for child in self.findChildren(QWidget):
            child_class_name = child.metaObject().className()

            # Buttons: enforce minimum touch target
            if "Button" in child_class_name:
                min_size = tokens.MIN_TOUCH_TARGET
                if child.minimumWidth() < min_size:
                    child.setMinimumWidth(min_size)
                if child.minimumHeight() < min_size:
                    child.setMinimumHeight(min_size)

            # Line edits: enforce comfortable height
            elif "LineEdit" in child_class_name or "TextEdit" in child_class_name:
                child.setMinimumHeight(tokens.MIN_TOUCH_TARGET)

            # Labels in lists: ensure adequate spacing
            elif "Label" in child_class_name:
                current_font = child.font()
                if current_font.pointSize() < 13:
                    current_font.setPointSize(13)
                    child.setFont(current_font)


# ---------------------------------------------------------------------------
# Responsive layout manager
# ---------------------------------------------------------------------------

class ResponsiveLayoutManager:
    """Manages adaptive layout that switches between mobile and desktop.

    Monitors the window size and switches between:
    - Compact layout (< 600px width): single-panel, bottom nav, drawer
    - Expanded layout (>= 600px width): side-by-side panels, sidebar

    Usage:
        layout_mgr = ResponsiveLayoutManager(main_window)
        layout_mgr.set_compact_widget(mobile_view)
        layout_mgr.set_expanded_widget(desktop_view)
        layout_mgr.activate()
    """

    def __init__(self, window: QWidget) -> None:
        self._window = window
        self._compact_widget: Optional[QWidget] = None
        self._expanded_widget: Optional[QWidget] = None
        self._is_compact = False
        self._breakpoint = 600

    def set_compact_widget(self, widget: QWidget) -> None:
        """Set the widget to display in compact (mobile) mode."""
        self._compact_widget = widget

    def set_expanded_widget(self, widget: QWidget) -> None:
        """Set the widget to display in expanded (desktop) mode."""
        self._expanded_widget = widget

    def set_breakpoint(self, width: int) -> None:
        """Set the width breakpoint for layout switching."""
        self._breakpoint = width

    def activate(self) -> None:
        """Start monitoring window size changes."""
        self._window.installEventFilter(self)
        self._check_layout()

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        """Monitor resize events to switch layouts."""
        if obj is self._window and event.type() == event.Type.Resize:
            self._check_layout()
        return False

    def _check_layout(self) -> None:
        """Check window width and switch layout if needed."""
        width = self._window.width()
        should_be_compact = width < self._breakpoint

        if should_be_compact == self._is_compact:
            return  # No change needed

        self._is_compact = should_be_compact
        self._switch_layout()

    def _switch_layout(self) -> None:
        """Switch between compact and expanded layouts."""
        if self._is_compact:
            if self._expanded_widget:
                self._expanded_widget.hide()
            if self._compact_widget:
                self._compact_widget.show()
        else:
            if self._compact_widget:
                self._compact_widget.hide()
            if self._expanded_widget:
                self._expanded_widget.show()

    @property
    def is_compact(self) -> bool:
        """True if currently in compact (mobile) layout."""
        return self._is_compact
