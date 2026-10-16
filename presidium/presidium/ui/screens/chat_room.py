"""Chat room screen for Presidium.

The main chat interface with:
- Peer info header (name, status, call button)
- Message area with bubble rendering and smooth scrolling
- Message input with send button
- File attachment support (button + drag-and-drop)
- Voice call initiation
- Reply mode with preview panel above input
- Drag-and-drop visual feedback (drop zone highlight)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QUrl
from PySide6.QtGui import QFont, QKeyEvent, QDragEnterEvent, QDropEvent, QColor
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
    QFrame,
)

from presidium.core.messaging.chat import ChatMessage, ChatSession, MessageStatus
from presidium.ui.components.chat_bubble import ChatBubble
from presidium.ui.components.identicon import generate_identicon_pixmap


# ---------------------------------------------------------------------------
# Reply preview panel
# ---------------------------------------------------------------------------

class ReplyPreviewPanel(QWidget):
    """Panel shown above the input when replying to a message.

    Displays a preview of the message being replied to with a cancel button.

    Signals:
        cancelled(): Emitted when the user cancels the reply.
    """

    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reply_to: Optional[ChatMessage] = None
        self.setObjectName("glass-panel")
        self._setup_ui()
        self.hide()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(8)

        # Accent bar on the left
        accent = QFrame()
        accent.setFixedWidth(3)
        accent.setStyleSheet("background-color: #6366F1; border-radius: 1px;")
        layout.addWidget(accent)

        # Reply info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        self._author_label = QLabel("")
        self._author_label.setObjectName("caption")
        author_font = QFont()
        author_font.setBold(True)
        self._author_label.setFont(author_font)
        self._author_label.setStyleSheet("color: #6366F1;")
        info_layout.addWidget(self._author_label)

        self._text_label = QLabel("")
        self._text_label.setObjectName("caption")
        self._text_label.setMaximumHeight(36)
        info_layout.addWidget(self._text_label)

        layout.addLayout(info_layout, 1)

        # Cancel button
        cancel_btn = QPushButton("\u2715")
        cancel_btn.setObjectName("icon-button")
        cancel_btn.setFixedSize(24, 24)
        cancel_btn.setToolTip("Cancel reply")
        cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(cancel_btn)

    def set_reply_to(self, message: ChatMessage) -> None:
        """Set the message being replied to."""
        self._reply_to = message
        self._author_label.setText(message.sender_user_id[:8] if not message.is_mine else "You")
        self._text_label.setText(message.content[:100] + ("..." if len(message.content) > 100 else ""))
        self.show()

    def clear_reply(self) -> None:
        """Clear the reply target."""
        self._reply_to = None
        self._author_label.setText("")
        self._text_label.setText("")
        self.hide()

    @property
    def reply_to(self) -> Optional[ChatMessage]:
        return self._reply_to

    def _on_cancel(self) -> None:
        self.clear_reply()
        self.cancelled.emit()


# ---------------------------------------------------------------------------
# Message area with drag-and-drop support
# ---------------------------------------------------------------------------

class MessageArea(QScrollArea):
    """Scrollable area displaying chat messages with drag-and-drop file support."""

    files_dropped = Signal(list)  # list of file paths

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

        # Drag-and-drop
        self.setAcceptDrops(True)
        self._drag_active = False

        # Drop zone overlay
        self._drop_overlay: Optional[QLabel] = None

    def add_bubble(self, bubble: ChatBubble) -> None:
        """Add a chat bubble and scroll to bottom."""
        self._layout.insertWidget(self._layout.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def scroll_to_message(self, message_id_hex: str) -> None:
        """Scroll to a specific message by ID."""
        # PHASE3: simplified — would need a message_id → widget map
        self._scroll_to_bottom()

    def clear_bubbles(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Drag-and-drop ──

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept file drags."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            # Check if all URLs are local files
            if all(url.isLocalFile() for url in urls):
                event.acceptProposedAction()
                self._show_drop_overlay()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        """Keep accepting during drag move."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        """Remove overlay when drag leaves."""
        self._hide_drop_overlay()

    def dropEvent(self, event: QDropEvent) -> None:
        """Handle file drop — emit file paths."""
        self._hide_drop_overlay()
        if event.mimeData().hasUrls():
            file_paths = []
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path and Path(path).is_file():
                    file_paths.append(path)
            if file_paths:
                self.files_dropped.emit(file_paths)
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()

    def _show_drop_overlay(self) -> None:
        """Show a semi-transparent overlay indicating the drop zone."""
        if self._drop_overlay is not None:
            return
        self._drop_overlay = QLabel("Drop files here", self)
        self._drop_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_overlay.setStyleSheet("""
            background-color: rgba(99, 102, 241, 0.15);
            color: #818CF8;
            border: 2px dashed #6366F1;
            border-radius: 16px;
            font-size: 18px;
            font-weight: bold;
        """)
        self._drop_overlay.setGeometry(self.rect())
        self._drop_overlay.raise_()
        self._drop_overlay.show()

    def _hide_drop_overlay(self) -> None:
        """Remove the drop overlay."""
        if self._drop_overlay is not None:
            self._drop_overlay.deleteLater()
            self._drop_overlay = None


# ---------------------------------------------------------------------------
# Message input with reply support
# ---------------------------------------------------------------------------

class MessageInput(QWidget):
    """Message input bar with send button, attachment, and reply support.

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
        self._reply_panel = ReplyPreviewPanel(self)
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Reply preview (hidden by default)
        outer.addWidget(self._reply_panel)

        # Input row
        row = QHBoxLayout()
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(8)

        # Attach button
        attach_btn = QPushButton("\U0001f4ce")
        attach_btn.setObjectName("icon-button")
        attach_btn.setFixedSize(40, 40)
        attach_btn.setToolTip("Attach file")
        attach_btn.clicked.connect(self.attach_file.emit)
        row.addWidget(attach_btn)

        # Text input
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a message...")
        self._input.setMinimumHeight(40)
        self._input.returnPressed.connect(self._on_send)
        row.addWidget(self._input, 1)

        # Voice call button
        call_btn = QPushButton("\U0001f4de")
        call_btn.setObjectName("icon-button")
        call_btn.setFixedSize(40, 40)
        call_btn.setToolTip("Voice call")
        call_btn.clicked.connect(self.voice_call.emit)
        row.addWidget(call_btn)

        # Send button
        send_btn = QPushButton("\u279c")
        send_btn.setFixedSize(40, 40)
        send_btn.setToolTip("Send message")
        send_btn.clicked.connect(self._on_send)
        row.addWidget(send_btn)

        outer.addLayout(row)

    def _on_send(self) -> None:
        """Send the current message text."""
        text = self._input.text().strip()
        if text:
            self.send_message.emit(text)
            self._input.clear()
            self._reply_panel.clear_reply()

    def clear(self) -> None:
        self._input.clear()
        self.clear_reply()

    def set_enabled(self, enabled: bool) -> None:
        self._input.setEnabled(enabled)

    def set_reply_to(self, message: ChatMessage) -> None:
        """Set a message to reply to."""
        self._reply_panel.set_reply_to(message)
        self._input.setFocus()

    def clear_reply(self) -> None:
        """Clear the reply target."""
        self._reply_panel.clear_reply()

    @property
    def reply_to_id(self) -> bytes:
        """Get the message_id being replied to, or b""."""
        if self._reply_panel.reply_to:
            return self._reply_panel.reply_to.message_id
        return b""


# ---------------------------------------------------------------------------
# Chat Room Screen
# ---------------------------------------------------------------------------

class ChatRoomScreen(QWidget):
    """Full chat room screen with message display, input, drag-drop, and reply.

    Signals:
        send_message(str, str, bytes): peer_user_id, text, reply_to_message_id
        attach_file(str): peer_user_id
        send_files(str, list): peer_user_id, list of file paths
        voice_call(str): peer_user_id
        back_requested(): Emitted when the user wants to go back.
    """

    send_message = Signal(str, str, bytes)  # peer_user_id, text, reply_to
    attach_file = Signal(str)               # peer_user_id
    send_files = Signal(str, list)          # peer_user_id, file_paths
    voice_call = Signal(str)                # peer_user_id
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session: ChatSession | None = None
        self._bubble_map: dict[str, ChatBubble] = {}
        self._message_by_id: dict[str, ChatMessage] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QWidget()
        header.setMinimumHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)

        back_btn = QPushButton("\u2190")
        back_btn.setObjectName("icon-button")
        back_btn.setFixedSize(36, 36)
        back_btn.clicked.connect(self.back_requested.emit)
        header_layout.addWidget(back_btn)

        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(36, 36)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self._avatar_label)

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
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ── Message area (with drag-drop) ──
        self._message_area = MessageArea()
        self._message_area.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self._message_area, 1)

        # ── Input ──
        self._input = MessageInput()
        self._input.send_message.connect(self._on_send)
        self._input.attach_file.connect(self._on_attach)
        self._input.voice_call.connect(self._on_call)
        layout.addWidget(self._input)

        self._input.set_enabled(False)

    @property
    def current_peer(self) -> str | None:
        return self._session.peer_user_id if self._session else None

    def clear_session(self) -> None:
        self._session = None
        self._message_area.clear_bubbles()
        self._bubble_map.clear()
        self._message_by_id.clear()
        self._input.clear()
        self._input.set_enabled(False)
        self._peer_name.clear()
        self._avatar_label.clear()

    def set_session(self, session: ChatSession) -> None:
        """Set the active chat session and load messages."""
        self._session = session
        self._peer_name.setText(session.display_name)

        try:
            if session.peer_public_key:
                pixmap = generate_identicon_pixmap(session.peer_public_key, 36)
                self._avatar_label.setPixmap(pixmap)
            else:
                self._avatar_label.setText("\U0001f464")
        except Exception:
            self._avatar_label.setText("\U0001f464")

        self._message_area.clear_bubbles()
        self._bubble_map.clear()
        self._message_by_id.clear()
        for msg in session.messages:
            self._add_bubble(msg)

        self._input.set_enabled(True)
        session.mark_all_read()

    def add_message(self, message: ChatMessage) -> None:
        """Add a new message to the display."""
        self._add_bubble(message)

    def _add_bubble(self, message: ChatMessage) -> None:
        """Create and add a chat bubble for a message."""
        # Look up the replied-to message for preview
        reply_preview = None
        if message.reply_to:
            reply_preview = self._message_by_id.get(message.reply_to.hex())

        bubble = ChatBubble(message, reply_message=reply_preview)
        bubble.reply_requested.connect(self._on_reply_requested)
        bubble.scroll_to_message.connect(self._on_scroll_to_message)
        self._bubble_map[message.message_id_hex] = bubble
        self._message_by_id[message.message_id_hex] = message
        self._message_area.add_bubble(bubble)

    def update_message_status(self, message_id_hex: str, status: MessageStatus) -> None:
        """Update the delivery status of a displayed message."""
        bubble = self._bubble_map.get(message_id_hex)
        if bubble:
            bubble.update_status(status)

    # ── Event handlers ──

    def _on_send(self, text: str) -> None:
        """Handle message send with optional reply_to."""
        if self._session:
            reply_to = self._input.reply_to_id
            self.send_message.emit(self._session.peer_user_id, text, reply_to)

    def _on_attach(self) -> None:
        """Handle file attachment via button."""
        if self._session:
            file_paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Select Files to Send",
                "",
                "All Files (*)",
            )
            if file_paths:
                self.send_files.emit(self._session.peer_user_id, file_paths)

    def _on_files_dropped(self, file_paths: list[str]) -> None:
        """Handle files dropped onto the message area."""
        if self._session:
            self.send_files.emit(self._session.peer_user_id, file_paths)

    def _on_call(self) -> None:
        """Handle voice call initiation."""
        if self._session:
            self.voice_call.emit(self._session.peer_user_id)

    def _on_reply_requested(self, message_id_hex: str) -> None:
        """Handle reply request from a chat bubble."""
        message = self._message_by_id.get(message_id_hex)
        if message:
            self._input.set_reply_to(message)

    def _on_scroll_to_message(self, message_id_hex: str) -> None:
        """Scroll to a specific message (e.g., from a reply bubble click)."""
        self._message_area.scroll_to_message(message_id_hex)
