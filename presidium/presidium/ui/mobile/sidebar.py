"""Mobile sidebar drawer for Presidium.

Implements a slide-out drawer that replaces the desktop sidebar on
mobile devices. The drawer slides in from the left edge and overlays
the main content with a semi-transparent scrim.

Features:
    - Smooth slide animation (250ms)
    - Scrim overlay that dismisses on tap
    - Profile section at top (avatar, name, user ID)
    - Chat list with search
    - Quick actions (new chat, settings, bridge status)
    - Edge swipe to open (detected by SwipeContainer)
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from presidium.ui.mobile.navigation import MobileTokens


# ---------------------------------------------------------------------------
# Mobile sidebar
# ---------------------------------------------------------------------------

class MobileSidebar(QWidget):
    """Slide-out navigation drawer for mobile.

    Signals:
        opened(): Drawer fully opened.
        closed(): Drawer fully closed.
        chat_selected(str): Chat peer ID selected.
        new_chat_clicked(): New chat button clicked.
        settings_clicked(): Settings button clicked.
    """

    opened = Signal()
    closed = Signal()
    chat_selected = Signal(str)
    new_chat_clicked = Signal()
    settings_clicked = Signal()

    DRAWER_WIDTH = 300

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tokens = MobileTokens()
        self._is_open = False

        # Scrim (semi-transparent overlay)
        self._scrim = QWidget(self)
        self._scrim.setStyleSheet("background-color: rgba(0, 0, 0, 0.5);")
        self._scrim.hide()
        self._scrim.mousePressEvent = lambda e: self.close()  # type: ignore[assignment]

        self._setup_ui()
        self._setup_animation()

    def _setup_ui(self) -> None:
        """Build the drawer content layout."""
        tokens = self._tokens

        # Drawer panel
        self._panel = QFrame(self)
        self._panel.setFixedWidth(self.DRAWER_WIDTH)
        self._panel.setStyleSheet(f"""
            QFrame {{
                background-color: {tokens.NAV_BACKGROUND};
                border-right: 1px solid #2C2B30;
            }}
        """)

        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(5, 0)
        shadow.setColor(QColor(0, 0, 0, 100))
        self._panel.setGraphicsEffect(shadow)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        # --- Profile section ---
        profile = QWidget()
        profile.setFixedHeight(180)
        profile_layout = QVBoxLayout(profile)
        profile_layout.setContentsMargins(20, 40, 20, 16)

        # Avatar placeholder
        avatar = QLabel("👤")
        avatar_font = QFont()
        avatar_font.setPointSize(40)
        avatar.setFont(avatar_font)
        avatar.setAlignment(Qt.AlignCenter)
        profile_layout.addWidget(avatar)

        # User name
        self._name_label = QLabel("Presidium User")
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        self._name_label.setFont(name_font)
        self._name_label.setStyleSheet("color: #E6E0E9; background: transparent;")
        profile_layout.addWidget(self._name_label)

        # User ID
        self._id_label = QLabel("xxxxxxxx")
        id_font = QFont()
        id_font.setPointSize(11)
        self._id_label.setFont(id_font)
        self._id_label.setStyleSheet("color: #938F99; background: transparent;")
        profile_layout.addWidget(self._id_label)

        profile.setStyleSheet(f"background-color: #2C2B30;")
        panel_layout.addWidget(profile)

        # --- Search bar ---
        search_container = QWidget()
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(12, 8, 12, 8)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Search chats...")
        search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: #2C2B30;
                color: #E6E0E9;
                border: 1px solid #49454F;
                border-radius: 20px;
                padding: 8px 16px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {tokens.NAV_ACTIVE};
            }}
        """)
        search_layout.addWidget(search_input)
        panel_layout.addWidget(search_container)

        # --- Chat list ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 3px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #49454F;
                border-radius: 2px;
            }
        """)

        self._chat_list = QWidget()
        self._chat_list_layout = QVBoxLayout(self._chat_list)
        self._chat_list_layout.setContentsMargins(0, 0, 0, 0)
        self._chat_list_layout.setSpacing(1)
        self._chat_list_layout.addStretch()
        scroll.setWidget(self._chat_list)
        panel_layout.addWidget(scroll, stretch=1)

        # --- Bottom actions ---
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(12, 8, 12, 8)

        new_chat_btn = QPushButton("New Chat")
        new_chat_btn.setFixedHeight(tokens.MIN_TOUCH_TARGET)
        new_chat_btn.clicked.connect(self.new_chat_clicked.emit)
        new_chat_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {tokens.FAB_BACKGROUND};
                color: {tokens.FAB_FOREGROUND};
                border: none;
                border-radius: 24px;
                font-size: 14px;
                font-weight: bold;
                padding: 0 20px;
            }}
        """)
        actions_layout.addWidget(new_chat_btn)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(tokens.MIN_TOUCH_TARGET, tokens.MIN_TOUCH_TARGET)
        settings_btn.clicked.connect(self.settings_clicked.emit)
        settings_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #938F99;
                border: none;
                font-size: 22px;
            }
            QPushButton:hover { color: #E6E0E9; }
        """)
        actions_layout.addWidget(settings_btn)

        panel_layout.addWidget(actions)

    def _setup_animation(self) -> None:
        """Set up slide animation."""
        self._animation = QPropertyAnimation(self._panel, b"geometry")
        self._animation.setDuration(250)
        self._animation.setEasingCurve(
            # EaseOutCubic for smooth deceleration
            self._animation.EasingCurve(self._animation.EasingCurve.OutCubic)
        )

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def open(self) -> None:
        """Slide the drawer open from the left."""
        if self._is_open:
            return

        self._is_open = True
        self._scrim.setGeometry(self.geometry())
        self._scrim.show()
        self._scrim.raise_()

        # Position panel off-screen left
        screen_height = self.height()
        self._panel.setGeometry(
            -self.DRAWER_WIDTH, 0,
            self.DRAWER_WIDTH, screen_height,
        )
        self._panel.show()
        self._panel.raise_()

        # Animate to visible position
        self._animation.setStartValue(
            self._panel.geometry()
        )
        self._animation.setEndValue(
            self._panel.geometry().adjusted(self.DRAWER_WIDTH, 0, self.DRAWER_WIDTH, 0)
        )
        self._animation.start()
        self.opened.emit()

    def close(self) -> None:
        """Slide the drawer closed."""
        if not self._is_open:
            return

        self._is_open = False

        self._animation.setStartValue(self._panel.geometry())
        self._animation.setEndValue(
            self._panel.geometry().adjusted(-self.DRAWER_WIDTH, 0, -self.DRAWER_WIDTH, 0)
        )
        self._animation.start()

        # Hide scrim after animation
        from PySide6.QtCore import QTimer
        QTimer.singleShot(260, self._scrim.hide)

        self.closed.emit()

    def toggle(self) -> None:
        """Toggle the drawer open/closed state."""
        if self._is_open:
            self.close()
        else:
            self.open()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def set_user_info(self, name: str, user_id: str) -> None:
        """Update the profile section."""
        self._name_label.setText(name)
        self._id_label.setText(user_id[:16])

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Handle resize to update scrim and panel geometry."""
        super().resizeEvent(event)
        self._scrim.setGeometry(self.geometry())
        if self._is_open:
            self._panel.setGeometry(0, 0, self.DRAWER_WIDTH, self.height())
        else:
            self._panel.setGeometry(-self.DRAWER_WIDTH, 0, self.DRAWER_WIDTH, self.height())
