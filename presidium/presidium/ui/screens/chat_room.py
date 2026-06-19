"""Chat room screen for Presidium.

The main chat interface with:
- Peer info header (name, status, call button)
- Message area with bubble rendering and smooth scrolling
- Message input with send button
- File attachment support
- Voice call initiation
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer, QScrollArea
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QFileDialog,
)

from presidium.core.messaging.chat import ChatMessage, ChatSession, MessageStatus
from presidium.ui.components.chat_bubble import ChatBubble
from presidium.ui.components.identicon import generate_identicon_pixmap


class MessageArea(QScrollArea):
    """Scrollable area displaying chat messages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(4)
        self._layout.addStretch()

        self.setWidget(self._container)

    def add_bubble(self, bubble: ChatBubble) -> None:
        """Add a chat bubble and scroll to bottom."""
        self._layout.insertWidget(self._layout.count() - 1, bubble)
        # Auto-scroll to bottom
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        """Scroll the message area to the bottom."""
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_bubbles(self) -> None:
        """Remove all chat bubbles."""
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class MessageInput(QWidget):
    """Message input bar with send button and attachment support.

    Signals:
        send_message(str): Emitted when the user sends a message.
        attach_file(): Emitted when the user wants to attach a file.
        voice_call(): Emitted when the user wants to start a voice call.
    """

    send_message = Signal(str)
    attach_file = Signal()
    voice_call = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        # Attach button
        attach_btn = QPushButton("\U0001f4ce")
        attach_btn.setObjectName("icon-button")
        attach_btn.setFixedSize(40, 40)
        attach_btn.setToolTip("Attach file")
        attach_btn.clicked.connect(self.attach_file.emit)
        layout.addWidget(attach_btn)

        # Text input
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a message...")
        self._input.setMinimumHeight(40)
        self._input.returnPressed.connect(self._on_send)
        layout.addWidget(self._input, 1)

        # Voice call button
        call_btn = QPushButton("\U0001f4de")
        call_btn.setObjectName("icon-button")
        call_btn.setFixedSize(40, 40)
        call_btn.setToolTip("Voice call")
        call_btn.clicked.connect(self.voice_call.emit)
        layout.addWidget(call_btn)

        # Send button
        send_btn = QPushButton("\u279c")
        send_btn.setFixedSize(40, 40)
        send_btn.setToolTip("Send message")
        send_btn.clicked.connect(self._on_send)
        layout.addWidget(send_btn)

    def _on_send(self) -> None:
        """Send the current message text."""
        text = self._input.text().strip()
        if text:
            self.send_message.emit(text)
            self._input.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the input."""
        self._input.setEnabled(enabled)


class ChatRoomScreen(QWidget):
    """Full chat room screen with message display and input.

    Signals:
        send_message(str, str): Emitted with (peer_user_id, text).
        attach_file(str): Emitted with peer_user_id.
        voice_call(str): Emitted with peer_user_id.
        back_requested(): Emitted when the user wants to go back.
    """

    send_message = Signal(str, str)  # peer_user_id, text
    attach_file = Signal(str)        # peer_user_id
    voice_call = Signal(str)         # peer_user_id
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: ChatSession | None = None
        self._bubble_map: dict[str, ChatBubble] = {}  # message_id_hex → bubble
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the screen layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QWidget()
        header.setMinimumHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)

        # Back button (for mobile/narrow layouts)
        back_btn = QPushButton("\u2190")
        back_btn.setObjectName("icon-button")
        back_btn.setFixedSize(36, 36)
        back_btn.clicked.connect(self.back_requested.emit)
        header_layout.addWidget(back_btn)

        # Peer avatar
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(36, 36)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self._avatar_label)

        # Peer name + status
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        self._peer_name = QLabel("Select a chat")
        peer_font = QFont()
        peer_font.setPointSize(14)
        peer_font.setWeight(QFont.Weight.Medium)
        self._peer_name.setFont(peer_font)
        info_layout.addWidget(self._peer_name)

        self._peer_status = QLabel("")
        self._peer_status.setObjectName("caption")
        info_layout.addWidget(self._peer_status)

        header_layout.addLayout(info_layout, 1)

        layout.addWidget(header)

        # ── Separator ──
        from PySide6.QtWidgets import QFrame
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ── Message area ──
        self._message_area = MessageArea()
        layout.addWidget(self._message_area, 1)

        # ── Input ──
        self._input = MessageInput()
        self._input.send_message.connect(self._on_send)
        self._input.attach_file.connect(self._on_attach)
        self._input.voice_call.connect(self._on_call)
        layout.addWidget(self._input)

        # Default: input disabled until a session is set
        self._input.set_enabled(False)

    def set_session(self, session: ChatSession) -> None:
        """Set the active chat session and load messages."""
        self._session = session

        # Update header
        self._peer_name.setText(session.display_name)

        # Generate avatar
        try:
            if session.peer_public_key:
                pixmap = generate_identicon_pixmap(session.peer_public_key, 36)
                self._avatar_label.setPixmap(pixmap)
            else:
                self._avatar_label.setText("\U0001f464")
        except Exception:
            self._avatar_label.setText("\U0001f464")

        # Load messages
        self._message_area.clear_bubbles()
        self._bubble_map.clear()
        for msg in session.messages:
            self._add_bubble(msg)

        # Enable input
        self._input.set_enabled(True)

        # Mark as read
        session.mark_all_read()

    def add_message(self, message: ChatMessage) -> None:
        """Add a new message to the display."""
        self._add_bubble(message)

    def _add_bubble(self, message: ChatMessage) -> None:
        """Create and add a chat bubble for a message."""
        bubble = ChatBubble(message)
        self._bubble_map[message.message_id_hex] = bubble
        self._message_area.add_bubble(bubble)

    def update_message_status(self, message_id_hex: str, status: MessageStatus) -> None:
        """Update the delivery status of a displayed message."""
        bubble = self._bubble_map.get(message_id_hex)
        if bubble:
            bubble.update_status(status)

    # ── Event handlers ──

    def _on_send(self, text: str) -> None:
        """Handle message send."""
        if self._session:
            self.send_message.emit(self._session.peer_user_id, text)

    def _on_attach(self) -> None:
        """Handle file attachment."""
        if self._session:
            self.attach_file.emit(self._session.peer_user_id)

    def _on_call(self) -> None:
        """Handle voice call initiation."""
        if self._session:
            self.voice_call.emit(self._session.peer_user_id)
