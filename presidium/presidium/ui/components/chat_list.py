"""Chat list widget for Presidium.

Displays a scrollable list of conversation previews:
- Peer avatar (identicon)
- Peer display name
- Last message preview
- Relative timestamp
- Unread count badge
- Active state indicator
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from presidium.core.messaging.chat import ChatSession
from presidium.ui.components.identicon import generate_identicon_pixmap


class ChatListItem(QWidget):
    """A single chat session preview in the list.

    Signals:
        clicked(str): Emitted with peer_user_id when the item is clicked.
    """

    clicked = Signal(str)

    def __init__(
        self,
        session: ChatSession,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self.setObjectName("chat-list-item")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(72)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the item layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        # Avatar
        avatar_label = QLabel()
        avatar_label.setFixedSize(48, 48)
        avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            if self._session.peer_public_key:
                pixmap = generate_identicon_pixmap(self._session.peer_public_key, 48)
                avatar_label.setPixmap(pixmap)
            else:
                avatar_label.setText("\U0001f464")
        except Exception:
            avatar_label.setText("\U0001f464")
        layout.addWidget(avatar_label)

        # Text content
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        # Top row: name + timestamp
        top_row = QHBoxLayout()
        name_label = QLabel(self._session.display_name)
        name_font = QFont()
        name_font.setPointSize(13)
        name_font.setWeight(QFont.Weight.Medium)
        name_label.setFont(name_font)
        top_row.addWidget(name_label, 1)

        from presidium.ui.components.chat_bubble import format_timestamp
        if self._session.last_message_timestamp > 0:
            time_label = QLabel(format_timestamp(self._session.last_message_timestamp))
            time_label.setObjectName("caption")
            time_font = QFont()
            time_font.setPointSize(10)
            time_label.setFont(time_font)
            top_row.addWidget(time_label)

        text_layout.addLayout(top_row)

        # Bottom row: last message preview + unread badge
        bottom_row = QHBoxLayout()

        preview = self._session.last_message_preview or "No messages yet"
        preview_label = QLabel(preview)
        preview_label.setObjectName("subtitle")
        preview_font = QFont()
        preview_font.setPointSize(12)
        preview_label.setFont(preview_font)
        preview_label.setMaximumWidth(300)
        bottom_row.addWidget(preview_label, 1)

        if self._session.unread_count > 0:
            badge = QLabel(str(self._session.unread_count))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(24, 24)
            badge.setStyleSheet("""
                background-color: #6366F1;
                color: white;
                border-radius: 12px;
                font-size: 11px;
                font-weight: bold;
            """)
            bottom_row.addWidget(badge)

        text_layout.addLayout(bottom_row)
        layout.addLayout(text_layout, 1)

    def mousePressEvent(self, event) -> None:
        """Handle click to emit the peer user ID."""
        self.clicked.emit(self._session.peer_user_id)
        super().mousePressEvent(event)

    def update_session(self, session: ChatSession) -> None:
        """Update the displayed session data."""
        self._session = session
        # SIMPLIFIED: in production, update all child widgets


class ChatListWidget(QScrollArea):
    """Scrollable list of chat session previews.

    Signals:
        chat_selected(str): Emitted with peer_user_id when a chat is selected.
    """

    chat_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.addStretch()

        self.setWidget(self._container)

        self._items: dict[str, ChatListItem] = {}

    def set_sessions(self, sessions: list[ChatSession]) -> None:
        """Replace all chat list items with the given sessions."""
        # Clear existing
        for item in self._items.values():
            self._layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()

        # Add new items
        for session in sessions:
            self.add_session(session)

    def add_session(self, session: ChatSession) -> None:
        """Add or update a chat session in the list."""
        existing = self._items.get(session.peer_user_id)
        if existing:
            existing.update_session(session)
            return

        item = ChatListItem(session, self._container)
        item.clicked.connect(self.chat_selected.emit)
        # Insert before the stretch
        self._layout.insertWidget(self._layout.count() - 1, item)
        self._items[session.peer_user_id] = item

    def remove_session(self, peer_user_id: str) -> None:
        """Remove a chat session from the list."""
        item = self._items.pop(peer_user_id, None)
        if item:
            self._layout.removeWidget(item)
            item.deleteLater()

    def highlight_session(self, peer_user_id: str) -> None:
        """Highlight a specific chat session (e.g., when a new message arrives)."""
        item = self._items.get(peer_user_id)
        if item:
            # Bring to top
            self._layout.removeWidget(item)
            self._layout.insertWidget(0, item)

    def sizeHint(self) -> QSize:
        return QSize(360, 600)
