"""Sidebar widget for Presidium.

A collapsible navigation sidebar with:
- App branding / logo area
- Navigation items (Chats, Contacts, Settings)
- Active state indicator
- Collapse/expand toggle
- User profile section at the bottom
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
    QSizePolicy,
)


class SidebarButton(QPushButton):
    """A sidebar navigation button with active state."""

    def __init__(
        self,
        text: str,
        icon_char: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar-item")
        self.setText(f"  {icon_char}  {text}" if icon_char else f"  {text}")
        self.setCheckable(True)
        self.setMinimumHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        self._active = value
        self.setProperty("active", "true" if value else "false")
        self.setChecked(value)
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QWidget):
    """Main application sidebar with navigation and branding.

    Signals:
        navigate_to(str): Emitted when a nav item is clicked. The string
                         is the screen name: "chats", "contacts", "settings".
    """

    navigate_to = Signal(str)

    # Navigation item IDs
    CHATS = "chats"
    CONTACTS = "contacts"
    SETTINGS = "settings"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setMinimumWidth(240)
        self.setMaximumWidth(300)
        self._collapsed = False
        self._buttons: dict[str, SidebarButton] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the sidebar layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(8)

        # ── App branding ──
        brand_layout = QHBoxLayout()
        brand_label = QLabel("P")
        brand_label.setObjectName("title")
        brand_label.setFixedSize(40, 40)
        brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_font = QFont()
        brand_font.setPointSize(20)
        brand_font.setBold(True)
        brand_label.setFont(brand_font)

        self._app_name = QLabel("Presidium")
        self._app_name.setObjectName("title")
        app_font = QFont()
        app_font.setPointSize(16)
        app_font.setBold(True)
        self._app_name.setFont(app_font)

        brand_layout.addWidget(brand_label)
        brand_layout.addWidget(self._app_name, 1)
        layout.addLayout(brand_layout)

        # ── Separator ──
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        layout.addSpacing(8)

        # ── Navigation items ──
        self._add_nav_item(self.CHATS, "Chats", "\U0001f4ac")
        self._add_nav_item(self.CONTACTS, "Contacts", "\U0001f465")
        self._add_nav_item(self.SETTINGS, "Settings", "\u2699\ufe0f")

        layout.addSpacing(8)

        # ── Unread badge for chats ──
        self._unread_badge = QLabel("")
        self._unread_badge.setObjectName("caption")
        self._unread_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._unread_badge)

        layout.addStretch()

        # ── User profile section ──
        profile_frame = QFrame()
        profile_layout = QHBoxLayout(profile_frame)
        profile_layout.setContentsMargins(8, 8, 8, 8)

        self._user_avatar = QLabel("\U0001f464")
        self._user_avatar.setFixedSize(36, 36)
        self._user_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._user_name = QLabel("Not connected")
        self._user_name.setObjectName("subtitle")

        profile_layout.addWidget(self._user_avatar)
        profile_layout.addWidget(self._user_name, 1)

        # Collapse toggle
        self._toggle_btn = QPushButton("\u00ab")
        self._toggle_btn.setObjectName("icon-button")
        self._toggle_btn.setFixedSize(28, 28)
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        profile_layout.addWidget(self._toggle_btn)

        layout.addWidget(profile_frame)

        # Set chats as default active
        self.set_active(self.CHATS)

    def _add_nav_item(self, item_id: str, text: str, icon_char: str) -> None:
        """Add a navigation item to the sidebar."""
        btn = SidebarButton(text, icon_char, self)
        btn.clicked.connect(lambda checked, iid=item_id: self._on_nav_clicked(iid))
        self._buttons[item_id] = btn
        self.layout().addWidget(btn)

    def _on_nav_clicked(self, item_id: str) -> None:
        """Handle navigation item click."""
        self.set_active(item_id)
        self.navigate_to.emit(item_id)

    def set_active(self, item_id: str) -> None:
        """Set the active navigation item."""
        for iid, btn in self._buttons.items():
            btn.active = iid == item_id

    def set_user_info(self, user_id: str, nickname: str = "") -> None:
        """Update the user profile section at the bottom of the sidebar."""
        display = nickname or user_id[:8]
        self._user_name.setText(display)

    def set_unread_count(self, count: int) -> None:
        """Update the unread messages badge."""
        if count > 0:
            self._unread_badge.setText(f"{count} unread messages")
        else:
            self._unread_badge.setText("")

    def _toggle_collapse(self) -> None:
        """Toggle sidebar collapse/expand."""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.setMinimumWidth(64)
            self.setMaximumWidth(64)
            self._app_name.hide()
            for btn in self._buttons.values():
                # Show only icon part
                parts = btn.text().split("  ")
                btn.setText(f"  {parts[1]}" if len(parts) > 1 else "  \u2022")
            self._user_name.hide()
            self._toggle_btn.setText("\u00bb")
        else:
            self.setMinimumWidth(240)
            self.setMaximumWidth(300)
            self._app_name.show()
            self._user_name.show()
            self._toggle_btn.setText("\u00ab")
            # Restore full text
            for iid, btn in self._buttons.items():
                labels = {self.CHATS: "Chats", self.CONTACTS: "Contacts", self.SETTINGS: "Settings"}
                icons = {self.CHATS: "\U0001f4ac", self.CONTACTS: "\U0001f465", self.SETTINGS: "\u2699\ufe0f"}
                btn.setText(f"  {icons[iid]}  {labels[iid]}")

    def sizeHint(self) -> QSize:
        return QSize(240, 600)
