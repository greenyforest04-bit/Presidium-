"""Chat list screen for Presidium.

Displays all conversations with search/filter functionality.
Provides actions for starting new conversations.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
)

from presidium.core.messaging.chat import ChatSession
from presidium.ui.components.chat_list import ChatListWidget


class ChatListScreen(QWidget):
    """Screen showing the list of all conversations.

    Signals:
        chat_opened(str): Emitted with peer_user_id when a chat is opened.
        new_chat_requested(): Emitted when the user wants to start a new chat.
    """

    chat_opened = Signal(str)
    new_chat_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the screen layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QWidget()
        header.setMinimumHeight(64)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 20, 12)

        title = QLabel("Chats")
        title.setObjectName("title")
        title_font = QFont()
        title_font.setPointSize(22)
        title_font.setBold(True)
        title.setFont(title_font)
        header_layout.addWidget(title)

        header_layout.addStretch()

        # New chat button
        new_chat_btn = QPushButton("+ New Chat")
        new_chat_btn.setMinimumHeight(36)
        new_chat_btn.clicked.connect(self.new_chat_requested.emit)
        header_layout.addWidget(new_chat_btn)

        layout.addWidget(header)

        # ── Search bar ──
        search_container = QWidget()
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(16, 8, 16, 8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search conversations...")
        self._search_input.setMinimumHeight(40)
        self._search_input.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search_input)

        layout.addWidget(search_container)

        # ── Chat list ──
        self._chat_list = ChatListWidget()
        self._chat_list.chat_selected.connect(self.chat_opened.emit)
        layout.addWidget(self._chat_list, 1)

        # ── Empty state ──
        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(16)

        empty_icon = QLabel("\U0001f4ac")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_font = QFont()
        empty_font.setPointSize(48)
        empty_icon.setFont(empty_font)
        empty_layout.addWidget(empty_icon)

        empty_text = QLabel("No conversations yet")
        empty_text.setObjectName("subtitle")
        empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_text)

        empty_hint = QLabel("Start a new chat to begin messaging")
        empty_hint.setObjectName("caption")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint)

        self._empty_state.setVisible(False)
        layout.addWidget(self._empty_state, 1)

    def set_sessions(self, sessions: list[ChatSession]) -> None:
        """Update the displayed chat sessions."""
        self._chat_list.set_sessions(sessions)
        self._empty_state.setVisible(len(sessions) == 0)
        self._chat_list.setVisible(len(sessions) > 0)

    def add_session(self, session: ChatSession) -> None:
        """Add or update a chat session."""
        self._chat_list.add_session(session)
        self._empty_state.setVisible(False)
        self._chat_list.setVisible(True)

    def _on_search(self, text: str) -> None:
        """Filter the chat list by search text."""
        # SIMPLIFIED: in production, filter the list model
        pass
