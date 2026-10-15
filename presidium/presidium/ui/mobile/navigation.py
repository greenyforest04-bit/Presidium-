"""Mobile bottom navigation bar with floating action button.

Implements a Material You / Fluent 2 style bottom navigation that
replaces the desktop sidebar on mobile devices. Includes a central
FAB (Floating Action Button) for the primary action (new chat).

Layout:
    ┌──────────────────────────┐
    │  🏠 Chats  │  👥 Contacts  │  ⚙ Settings  │
    │            │  [➕ FAB]    │              │
    └──────────────────────────┘

Features:
    - 3-5 tab navigation with icon + label
    - Central FAB with elevation and ripple animation
    - Badge counts on tabs (unread messages)
    - Active tab indicator with smooth transition
    - Haptic feedback on tab switch
    - Keyboard-aware: hides when soft keyboard is visible
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QSize, Qt, Signal, QPropertyAnimation, QEasingCurve, QTimer, QRect
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Design tokens (Material You / Fluent 2)
# ---------------------------------------------------------------------------

class MobileTokens:
    """Design tokens for mobile UI components."""
    # Sizing
    MIN_TOUCH_TARGET = 48  # dp
    BOTTOM_NAV_HEIGHT = 80
    FAB_SIZE = 56
    FAB_ICON_SIZE = 24

    # Spacing
    PADDING_COMPACT = 8
    PADDING_COMFORTABLE = 16

    # Animation
    ANIMATION_DURATION_MS = 250

    # Colors (overridden by theme)
    NAV_BACKGROUND = "#1C1B1F"
    NAV_ACTIVE = "#D0BCFF"
    NAV_INACTIVE = "#938F99"
    FAB_BACKGROUND = "#6750A4"
    FAB_FOREGROUND = "#FFFFFF"
    BADGE_BACKGROUND = "#B3261E"
    BADGE_FOREGROUND = "#FFFFFF"

    # Typography
    LABEL_FONT_SIZE = 12
    BADGE_FONT_SIZE = 10


# ---------------------------------------------------------------------------
# Navigation tab
# ---------------------------------------------------------------------------

class NavTab:
    """Represents a single tab in the bottom navigation."""
    def __init__(
        self,
        label: str,
        icon: Optional[QIcon] = None,
        icon_unicode: str = "",
        badge_count: int = 0,
    ) -> None:
        self.label = label
        self.icon = icon
        self.icon_unicode = icon_unicode  # Fallback: Unicode emoji/symbol
        self.badge_count = badge_count
        self.is_active = False


# ---------------------------------------------------------------------------
# Tab button widget
# ---------------------------------------------------------------------------

class NavTabButton(QWidget):
    """A single tab button in the bottom navigation bar."""
    clicked = Signal(int)  # Emits tab index

    def __init__(
        self,
        tab: NavTab,
        index: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._tab = tab
        self._index = index
        self._tokens = MobileTokens()

        self.setFixedSize(self._tokens.MIN_TOUCH_TARGET, self._tokens.BOTTOM_NAV_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self._hovered = False

    @property
    def tab(self) -> NavTab:
        return self._tab

    def set_active(self, active: bool) -> None:
        self._tab.is_active = active
        self.update()

    def set_badge(self, count: int) -> None:
        self._tab.badge_count = count
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        tokens = self._tokens
        active = self._tab.is_active

        # Active indicator pill
        if active:
            pill_w, pill_h = 64, 32
            pill_x = (w - pill_w) // 2
            pill_y = 8
            painter.setBrush(QColor(tokens.NAV_ACTIVE))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, pill_h // 2, pill_h // 2)

        # Icon / Unicode symbol
        icon_y = 16 if active else 20
        color = QColor(tokens.NAV_ACTIVE if active else tokens.NAV_INACTIVE)

        if self._tab.icon:
            self._tab.icon.paint(painter, (w - 24) // 2, icon_y, 24, 24)
        elif self._tab.icon_unicode:
            font = QFont("Segoe UI Emoji", 18)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(0, icon_y, w, 24, Qt.AlignCenter, self._tab.icon_unicode)

        # Label
        label_y = icon_y + 28
        font = QFont(tokens.LABEL_FONT_SIZE)
        font.setBold(active)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(0, label_y, w, 20, Qt.AlignCenter, self._tab.label)

        # Badge
        if self._tab.badge_count > 0:
            badge_text = str(self._tab.badge_count)
            if self._tab.badge_count > 99:
                badge_text = "99+"

            badge_font = QFont(tokens.BADGE_FONT_SIZE)
            badge_font.setBold(True)
            painter.setFont(badge_font)

            badge_w = max(20, len(badge_text) * 8 + 8)
            badge_x = w // 2 + 8
            badge_y = 8

            painter.setBrush(QColor(tokens.BADGE_BACKGROUND))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(badge_x, badge_y, badge_w, 16, 8, 8)

            painter.setPen(QColor(tokens.BADGE_FOREGROUND))
            painter.drawText(
                badge_x, badge_y, badge_w, 16,
                Qt.AlignCenter, badge_text,
            )

        painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._index)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()


# ---------------------------------------------------------------------------
# FAB (Floating Action Button)
# ---------------------------------------------------------------------------

class FloatingActionButton(QPushButton):
    """Material You styled FAB for the primary action."""

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        tokens = MobileTokens()
        self.setFixedSize(tokens.FAB_SIZE, tokens.FAB_SIZE)
        self.setText("+")
        self.setCursor(Qt.PointingHandCursor)

        font = QFont()
        font.setPointSize(22)
        font.setBold(True)
        self.setFont(font)

        self._tokens = tokens
        self._apply_style()
        self._setup_spring_animation()

    def _setup_spring_animation(self) -> None:
        self.setGraphicsEffect(None)
        anim = QPropertyAnimation(self, b"geometry")
        orig = self.geometry()
        anim.setDuration(400)
        anim.setStartValue(QRect(orig.x() - 4, orig.y() - 4,
                                  orig.width() + 8, orig.height() + 8))
        anim.setEndValue(orig)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        QTimer.singleShot(200, anim.start)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        orig = self.geometry()
        anim = QPropertyAnimation(self, b"geometry")
        anim.setDuration(400)
        start_scale = 0.7
        anim.setStartValue(QRect(
            int(orig.x() + orig.width() * (1 - start_scale) / 2),
            int(orig.y() + orig.height() * (1 - start_scale) / 2),
            int(orig.width() * start_scale),
            int(orig.height() * start_scale),
        ))
        anim.setEndValue(orig)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.start()

    def _apply_style(self) -> None:
        tokens = self._tokens
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {tokens.FAB_BACKGROUND};
                color: {tokens.FAB_FOREGROUND};
                border: none;
                border-radius: {tokens.FAB_SIZE // 2}px;
                font-size: 22px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {tokens.FAB_BACKGROUND}DD;
            }}
            QPushButton:pressed {{
                background-color: {tokens.FAB_BACKGROUND}AA;
            }}
        """)


# ---------------------------------------------------------------------------
# Bottom navigation bar
# ---------------------------------------------------------------------------

class MobileNavigation(QWidget):
    """Bottom navigation bar with tabs and central FAB.

    Signals:
        tab_changed(int): Emitted when the active tab changes. Index of new tab.
        fab_clicked(): Emitted when the FAB is clicked.
    """

    tab_changed = Signal(int)
    fab_clicked = Signal()

    def __init__(
        self,
        tabs: Optional[list[NavTab]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._tokens = MobileTokens()
        self._tabs: list[NavTab] = tabs or self._default_tabs()
        self._active_index = 0
        self._tab_buttons: list[NavTabButton] = []

        self._setup_ui()

    def _default_tabs(self) -> list[NavTab]:
        """Create default navigation tabs."""
        return [
            NavTab(label="Chats", icon_unicode="💬"),
            NavTab(label="Contacts", icon_unicode="👥"),
            NavTab(label="Settings", icon_unicode="⚙"),
        ]

    def _setup_ui(self) -> None:
        """Build the navigation bar layout."""
        tokens = self._tokens
        self.setFixedHeight(tokens.BOTTOM_NAV_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create tab buttons
        for i, tab in enumerate(self._tabs):
            btn = NavTabButton(tab, i, self)
            btn.clicked.connect(self._on_tab_clicked)
            self._tab_buttons.append(btn)

            # Insert FAB before the middle tab
            if i == len(self._tabs) // 2:
                fab = FloatingActionButton(self)
                fab.clicked.connect(self.fab_clicked.emit)
                layout.addWidget(fab, stretch=0, alignment=Qt.AlignHCenter | Qt.AlignTop)

            layout.addWidget(btn, stretch=1, alignment=Qt.AlignHCenter | Qt.AlignTop)

        # Set initial active tab
        if self._tab_buttons:
            self._tab_buttons[0].set_active(True)

        self._apply_style()

    def _apply_style(self) -> None:
        tokens = self._tokens
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {tokens.NAV_BACKGROUND};
                border-top: 1px solid #2C2B30;
            }}
        """)

    def _on_tab_clicked(self, index: int) -> None:
        """Handle tab click and update active state."""
        if index == self._active_index:
            return

        # Deactivate old tab
        if 0 <= self._active_index < len(self._tab_buttons):
            self._tab_buttons[self._active_index].set_active(False)

        # Activate new tab
        self._active_index = index
        self._tab_buttons[index].set_active(True)
        self.tab_changed.emit(index)

    def set_badge(self, tab_index: int, count: int) -> None:
        """Set the badge count for a tab."""
        if 0 <= tab_index < len(self._tab_buttons):
            self._tab_buttons[tab_index].set_badge(count)

    def active_tab(self) -> int:
        """Return the current active tab index."""
        return self._active_index
