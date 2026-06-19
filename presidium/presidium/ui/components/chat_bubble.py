"""Chat bubble widget for Presidium.

Renders individual chat messages as styled bubbles:
- Own messages: right-aligned, accent-colored background
- Peer messages: left-aligned, surface-colored background
- Grouped by time (tighter spacing within a time window)
- Fade-in animation on appearance
- Timestamp and status indicators
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
)

from presidium.core.messaging.chat import ChatMessage, MessageStatus


# Status indicator characters
STATUS_ICONS = {
    MessageStatus.SENDING: "\u23f3",    # hourglass
    MessageStatus.SENT: "\u2713",        # check
    MessageStatus.DELIVERED: "\u2713\u2713",  # double check
    MessageStatus.READ: "\u2713\u2713",  # double check (colored)
    MessageStatus.ERROR: "\u26a0",       # warning
}


def format_timestamp(ts: float) -> str:
    """Format a Unix timestamp as a relative or absolute time string."""
    now = time.time()
    diff = now - ts

    if diff < 60:
        return "just now"
    elif diff < 3600:
        minutes = int(diff / 60)
        return f"{minutes}m ago"
    elif diff < 86400:
        hours = int(diff / 3600)
        return f"{hours}h ago"
    else:
        from datetime import datetime
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M")


class ChatBubble(QWidget):
    """A single chat message bubble.

    Automatically styles itself based on whether the message is from
    the local user (right-aligned, accent) or a peer (left-aligned, surface).
    """

    def __init__(
        self,
        message: ChatMessage,
        parent: QWidget | None = None,
        show_timestamp: bool = True,
    ) -> None:
        super().__init__(parent)
        self._message = message
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self._setup_ui(show_timestamp)
        self._apply_animation()

    def _setup_ui(self, show_timestamp: bool) -> None:
        """Build the bubble layout."""
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

        # Message text
        text_label = QLabel(msg.content)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        text_label.setMaximumWidth(480)

        # Make links clickable
        text_label.setOpenExternalLinks(True)
        text_label.setTextFormat(Qt.TextFormat.PlainText)

        bubble_layout.addWidget(text_label)

        # Footer: timestamp + status
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

                # Color the status based on state
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
        # Start invisible
        self.setWindowOpacity(0.0)

        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(200)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.start()

    def update_status(self, status: MessageStatus) -> None:
        """Update the message delivery status display."""
        self._message.status = status
        # SIMPLIFIED: in production, find and update the status label widget

    @property
    def message(self) -> ChatMessage:
        return self._message

    def sizeHint(self) -> QSize:
        return QSize(0, 60)
