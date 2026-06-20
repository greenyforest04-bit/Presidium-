"""Chat bubble widget for Presidium.

Renders individual chat messages as styled bubbles:
- Own messages: right-aligned, accent-colored background
- Peer messages: left-aligned, surface-colored background
- Reply visualization: mini-bubble inside main bubble
- Grouped by time (tighter spacing within a time window)
- Fade-in animation on appearance
- Timestamp and status indicators
- Right-click context menu: Reply, Copy, Forward
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize, Signal
from PySide6.QtGui import QFont, QCursor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QMenu,
)

from presidium.core.messaging.chat import ChatMessage, MessageStatus


# Status indicator characters
STATUS_ICONS = {
    MessageStatus.SENDING: "\u23f3",
    MessageStatus.SENT: "\u2713",
    MessageStatus.DELIVERED: "\u2713\u2713",
    MessageStatus.READ: "\u2713\u2713",
    MessageStatus.ERROR: "\u26a0",
}


def format_timestamp(ts: float) -> str:
    """Format a Unix timestamp as a relative or absolute time string."""
    now = time.time()
    diff = now - ts
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    else:
        from datetime import datetime
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Reply mini-bubble (shown inside a main bubble)
# ---------------------------------------------------------------------------

class ReplyMiniBubble(QWidget):
    """A compact preview of the message being replied to.

    Shown inside the main chat bubble with:
    - Author name (accent color)
    - Content preview (max 2 lines)
    - Left accent border
    """

    clicked = Signal()

    def __init__(
        self,
        author: str,
        content: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_ui(author, content)

    def _setup_ui(self, author: str, content: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Accent bar
        accent = QLabel()
        accent.setFixedWidth(2)
        accent.setStyleSheet("background-color: #6366F1; border-radius: 1px;")
        accent.setFixedHeight(32)
        layout.addWidget(accent)

        # Text
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        text_layout.setContentsMargins(0, 2, 0, 2)

        author_label = QLabel(author[:16])
        author_label.setStyleSheet("color: #6366F1; font-size: 11px; font-weight: bold;")
        text_layout.addWidget(author_label)

        # Limit to 2 lines
        preview = content[:80] + ("..." if len(content) > 80 else "")
        content_label = QLabel(preview)
        content_label.setStyleSheet("color: #8888A8; font-size: 11px;")
        content_label.setMaximumHeight(28)
        content_label.setWordWrap(True)
        text_layout.addWidget(content_label)

        layout.addLayout(text_layout, 1)

        self.setStyleSheet("background-color: rgba(36, 36, 54, 0.6); border-radius: 6px; padding: 4px 8px;")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Chat Bubble
# ---------------------------------------------------------------------------

class ChatBubble(QWidget):
    """A single chat message bubble with reply support.

    Automatically styles itself based on whether the message is from
    the local user (right-aligned, accent) or a peer (left-aligned, surface).

    If the message is a reply, a mini-bubble is shown inside.

    Signals:
        reply_requested(str): Emitted with message_id_hex when reply is requested.
        scroll_to_message(str): Emitted with message_id_hex to scroll to original.
    """

    reply_requested = Signal(str)
    scroll_to_message = Signal(str)

    def __init__(
        self,
        message: ChatMessage,
        show_timestamp: bool = True,
        reply_message: Optional[ChatMessage] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._message = message
        self._reply_message = reply_message
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self._setup_ui(show_timestamp)
        self._apply_animation()

    def _setup_ui(self, show_timestamp: bool) -> None:
        msg = self._message
        is_mine = msg.is_mine

        outer = QHBoxLayout(self)
        outer.setContentsMargins(
            0 if not is_mine else 48,
            2,
            0 if is_mine else 48,
            2,
        )

        # Bubble container
        bubble = QWidget()
        bubble.setObjectName("bubble-self" if is_mine else "bubble-peer")
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 8)
        bubble_layout.setSpacing(4)

        # ── Reply mini-bubble ──
        if msg.reply_to:
            reply_author = "You"
            reply_content = ""
            if self._reply_message:
                reply_author = self._reply_message.sender_user_id[:8] if not self._reply_message.is_mine else "You"
                reply_content = self._reply_message.content
            else:
                reply_content = f"Message {msg.reply_to.hex()[:8]}..."

            reply_bubble = ReplyMiniBubble(reply_author, reply_content)
            reply_bubble.clicked.connect(
                lambda: self.scroll_to_message.emit(msg.reply_to.hex())
            )
            bubble_layout.addWidget(reply_bubble)

        # ── Message text ──
        text_label = QLabel(msg.content)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        text_label.setMaximumWidth(480)
        text_label.setOpenExternalLinks(True)
        text_label.setTextFormat(Qt.TextFormat.PlainText)
        bubble_layout.addWidget(text_label)

        # ── Footer: timestamp + status ──
        if show_timestamp:
            footer = QHBoxLayout()
            footer.setSpacing(8)

            time_label = QLabel(format_timestamp(msg.timestamp))
            time_label.setObjectName("caption")
            time_font = QFont()
            time_font.setPointSize(9)
            time_label.setFont(time_font)
            footer.addWidget(time_label)

            if is_mine:
                status_text = STATUS_ICONS.get(msg.status, "")
                status_label = QLabel(status_text)
                status_label.setObjectName("caption")
                if msg.status == MessageStatus.READ:
                    status_label.setStyleSheet("color: #6366F1;")
                elif msg.status == MessageStatus.ERROR:
                    status_label.setStyleSheet("color: #F87171;")
                else:
                    status_label.setStyleSheet("color: #8888A8;")
                status_font = QFont()
                status_font.setPointSize(9)
                status_label.setFont(status_font)
                footer.addWidget(status_label)

            footer.addStretch()
            bubble_layout.addLayout(footer)

        # Add bubble to outer layout
        if is_mine:
            outer.addStretch()
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
            outer.addStretch()

    def _apply_animation(self) -> None:
        """Apply fade-in animation."""
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(200)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.start()

    def _show_context_menu(self, pos) -> None:
        """Show right-click context menu."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1A1A24;
                color: #E0E0EC;
                border: 1px solid #3A3A50;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #242436;
            }
        """)

        reply_action = menu.addAction("Reply")
        copy_action = menu.addAction("Copy Text")
        menu.addSeparator()
        forward_action = menu.addAction("Forward")

        action = menu.exec(QCursor.pos())

        if action == reply_action:
            self.reply_requested.emit(self._message.message_id_hex)
        elif action == copy_action:
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(self._message.content)
        elif action == forward_action:
            # PHASE3: simplified — forward not yet implemented
            pass

    def update_status(self, status: MessageStatus) -> None:
        """Update the message delivery status display."""
        self._message.status = status

    @property
    def message(self) -> ChatMessage:
        return self._message

    def sizeHint(self) -> QSize:
        return QSize(0, 60)
