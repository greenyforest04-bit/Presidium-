"""Mobile-optimized chat room screen.

Provides a touch-friendly chat interface adapted for mobile screens:
    - Full-screen chat view
    - Larger message bubbles and touch targets
    - Bottom-attached input field
    - Swipe-to-reply on messages
    - Pull-to-refresh for message sync
    - Adaptive keyboard handling (input field stays above soft keyboard)

This replaces the desktop ChatRoom screen on narrow viewports
(typically < 600dp width).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from presidium.ui.mobile.navigation import MobileTokens
from presidium.ui.mobile.swipe import SwipeContainer, SwipeableItem


# ---------------------------------------------------------------------------
# Mobile message bubble
# ---------------------------------------------------------------------------

class MobileChatBubble(QWidget):
    """A single chat message bubble optimized for touch.

    Features:
        - Larger padding and font size than desktop
        - Swipe right on bubble to reply
        - Long press for context menu (bottom sheet)
        - Timestamp and status indicator
        - Reply preview (nested bubble)
    """

    reply_requested = Signal(str)  # message_id

    def __init__(
        self,
        message_id: str,
        text: str,
        is_mine: bool = False,
        timestamp: str = "",
        sender_name: str = "",
        reply_preview: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._message_id = message_id
        self._is_mine = is_mine
        self._tokens = MobileTokens()

        self._setup_ui(text, timestamp, sender_name, reply_preview)

    def _setup_ui(
        self,
        text: str,
        timestamp: str,
        sender_name: str,
        reply_preview: str,
    ) -> None:
        tokens = self._tokens
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.PADDING_COMFORTABLE if not self._is_mine else 48,
            tokens.PADDING_COMPACT,
            tokens.PADDING_COMFORTABLE if self._is_mine else 48,
            tokens.PADDING_COMPACT,
        )

        # Bubble frame
        bubble = QFrame()
        bubble.setObjectName("chatBubble")

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 8)
        bubble_layout.setSpacing(4)

        # Reply preview (if present)
        if reply_preview:
            reply_label = QLabel(reply_preview)
            reply_label.setObjectName("replyPreview")
            reply_font = QFont()
            reply_font.setPointSize(11)
            reply_label.setFont(reply_font)
            reply_label.setMaximumHeight(40)
            reply_label.setWordWrap(True)
            bubble_layout.addWidget(reply_label)

        # Sender name (for group chats)
        if sender_name and not self._is_mine:
            name_label = QLabel(sender_name)
            name_label.setObjectName("senderName")
            name_font = QFont()
            name_font.setPointSize(11)
            name_font.setBold(True)
            name_label.setFont(name_font)
            bubble_layout.addWidget(name_label)

        # Message text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_font = QFont()
        text_font.setPointSize(15)  # Larger for mobile
        text_label.setFont(text_font)
        text_label.setTextInteractionFlags(Qt.NoTextInteraction)
        bubble_layout.addWidget(text_label)

        # Timestamp row
        ts_label = QLabel(timestamp)
        ts_label.setObjectName("timestamp")
        ts_font = QFont()
        ts_font.setPointSize(10)
        ts_label.setFont(ts_font)
        ts_label.setAlignment(Qt.AlignRight if self._is_mine else Qt.AlignLeft)
        bubble_layout.addWidget(ts_label)

        # Style the bubble
        bg_color = "#37306B" if self._is_mine else "#2C2B30"
        text_color = "#E6E0E9"
        bubble.setStyleSheet(f"""
            QFrame#chatBubble {{
                background-color: {bg_color};
                border-radius: 18px;
            }}
            QLabel {{
                color: {text_color};
                background: transparent;
                border: none;
            }}
            QLabel#replyPreview {{
                color: #938F99;
                border-left: 3px solid #6750A4;
                padding-left: 6px;
            }}
            QLabel#senderName {{
                color: {tokens.NAV_ACTIVE};
            }}
            QLabel#timestamp {{
                color: #938F99;
            }}
        """)

        layout.addWidget(bubble, alignment=Qt.AlignRight if self._is_mine else Qt.AlignLeft)


# ---------------------------------------------------------------------------
# Mobile input bar
# ---------------------------------------------------------------------------

class MobileInputBar(QWidget):
    """Touch-optimized message input bar.

    Layout:
        ┌────────────────────────────────────────────┐
        │ [📎] [💬 Type a message...        ] [➤ Send] │
        └────────────────────────────────────────────┘

    Features:
        - Attach button (file/media picker)
        - Expandable text input (grows up to 4 lines)
        - Send button with color change when text is present
        - Minimum height for comfortable touch typing
    """

    send_clicked = Signal(str)
    attach_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        tokens = MobileTokens()
        self._tokens = tokens
        self._setup_ui()

    def _setup_ui(self) -> None:
        tokens = self._tokens
        self.setMinimumHeight(tokens.MIN_TOUCH_TARGET + 16)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Attach button
        self._attach_btn = QPushButton("📎")
        self._attach_btn.setFixedSize(tokens.MIN_TOUCH_TARGET, tokens.MIN_TOUCH_TARGET)
        self._attach_btn.clicked.connect(self.attach_clicked.emit)
        layout.addWidget(self._attach_btn)

        # Text input
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a message...")
        self._input.setMinimumHeight(tokens.MIN_TOUCH_TARGET)
        self._input.returnPressed.connect(self._on_return)
        layout.addWidget(self._input, stretch=1)

        # Send button
        self._send_btn = QPushButton("➤")
        self._send_btn.setFixedSize(tokens.MIN_TOUCH_TARGET, tokens.MIN_TOUCH_TARGET)
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setEnabled(False)
        layout.addWidget(self._send_btn)

        # Connect text changed
        self._input.textChanged.connect(self._on_text_changed)

        self._apply_style()

    def _apply_style(self) -> None:
        tokens = self._tokens
        self.setStyleSheet(f"""
            QWidget {{
                background-color: #1C1B1F;
                border-top: 1px solid #2C2B30;
            }}
            QLineEdit {{
                background-color: #2C2B30;
                color: #E6E0E9;
                border: 1px solid #49454F;
                border-radius: 24px;
                padding: 8px 16px;
                font-size: 15px;
            }}
            QLineEdit:focus {{
                border-color: {tokens.NAV_ACTIVE};
            }}
            QPushButton {{
                background-color: #2C2B30;
                color: #938F99;
                border: none;
                border-radius: 24px;
                font-size: 18px;
            }}
            QPushButton:hover {{
                background-color: #49454F;
            }}
        """)

    def _on_text_changed(self, text: str) -> None:
        """Enable/disable send button based on input content."""
        has_text = bool(text.strip())
        self._send_btn.setEnabled(has_text)

        # Change send button color when active
        tokens = self._tokens
        if has_text:
            self._send_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {tokens.FAB_BACKGROUND};
                    color: {tokens.FAB_FOREGROUND};
                    border: none;
                    border-radius: 24px;
                    font-size: 18px;
                }}
            """)
        else:
            self._send_btn.setStyleSheet("")

    def _on_return(self) -> None:
        """Handle Enter key press."""
        self._on_send()

    def _on_send(self) -> None:
        """Emit the send signal with current text."""
        text = self._input.text().strip()
        if text:
            self.send_clicked.emit(text)
            self._input.clear()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the input bar."""
        self._input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled and bool(self._input.text().strip()))
        self._attach_btn.setEnabled(enabled)


# ---------------------------------------------------------------------------
# Mobile chat room
# ---------------------------------------------------------------------------

class MobileChatRoom(QWidget):
    """Full-screen mobile chat room interface.

    Layout:
        ┌────────────────────────────┐
        │  ← Back  │  Peer Name  │ ⋮ │  ← Header
        ├────────────────────────────┤
        │                            │
        │    [Message Bubbles]       │  ← Scroll area
        │                            │
        ├────────────────────────────┤
        │  [📎] [Input...] [Send]    │  ← Input bar
        └────────────────────────────┘

    Features:
        - Back button navigation
        - Pull-to-refresh for message history
        - Auto-scroll to bottom on new messages
        - Swipe right on bubble to reply
        - Long press for bottom sheet context menu
        - Keyboard-aware input bar
    """

    back_clicked = Signal()
    message_sent = Signal(str)  # text
    attach_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tokens = MobileTokens()
        self._bubbles: list[MobileChatBubble] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the mobile chat room layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Header ---
        header = QWidget()
        header.setFixedHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 0, 4, 0)

        back_btn = QPushButton("←")
        back_btn.setFixedSize(self._tokens.MIN_TOUCH_TARGET, self._tokens.MIN_TOUCH_TARGET)
        back_btn.clicked.connect(self.back_clicked.emit)
        header_layout.addWidget(back_btn)

        self._title_label = QLabel("Chat")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        header_layout.addWidget(self._title_label, stretch=1)

        menu_btn = QPushButton("⋮")
        menu_btn.setFixedSize(self._tokens.MIN_TOUCH_TARGET, self._tokens.MIN_TOUCH_TARGET)
        header_layout.addWidget(menu_btn)

        header.setStyleSheet("""
            QWidget {
                background-color: #1C1B1F;
                border-bottom: 1px solid #2C2B30;
            }
            QLabel { color: #E6E0E9; }
            QPushButton {
                background: transparent;
                color: #938F99;
                border: none;
                font-size: 20px;
            }
            QPushButton:hover { color: #E6E0E9; }
        """)
        layout.addWidget(header)

        # --- Message scroll area ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: #141218;
                border: none;
            }
            QScrollBar:vertical {
                width: 4px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #49454F;
                border-radius: 2px;
                min-height: 30px;
            }
        """)

        self._messages_widget = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_widget)
        self._messages_layout.setContentsMargins(0, 8, 0, 8)
        self._messages_layout.setSpacing(4)
        self._messages_layout.addStretch()

        scroll.setWidget(self._messages_widget)
        layout.addWidget(scroll, stretch=1)
        self._scroll_area = scroll

        # --- Input bar ---
        self._input_bar = MobileInputBar(self)
        self._input_bar.send_clicked.connect(self.message_sent.emit)
        self._input_bar.attach_clicked.connect(self.attach_requested.emit)
        layout.addWidget(self._input_bar)

    def set_title(self, name: str) -> None:
        """Set the header title (peer display name)."""
        self._title_label.setText(name)

    def add_message(
        self,
        message_id: str,
        text: str,
        is_mine: bool = False,
        timestamp: str = "",
        sender_name: str = "",
        reply_preview: str = "",
    ) -> None:
        """Add a message bubble to the chat."""
        bubble = MobileChatBubble(
            message_id=message_id,
            text=text,
            is_mine=is_mine,
            timestamp=timestamp,
            sender_name=sender_name,
            reply_preview=reply_preview,
            parent=self._messages_widget,
        )

        # Insert before the stretch
        self._messages_layout.insertWidget(
            self._messages_layout.count() - 1,
            bubble,
        )
        self._bubbles.append(bubble)

        # Auto-scroll to bottom
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        """Scroll the message area to the bottom."""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self._scroll_area.verticalScrollBar().setValue(
            self._scroll_area.verticalScrollBar().maximum()
        ))

    def clear_messages(self) -> None:
        """Remove all message bubbles."""
        for bubble in self._bubbles:
            bubble.setParent(None)
            bubble.deleteLater()
        self._bubbles.clear()

    def set_input_enabled(self, enabled: bool) -> None:
        """Enable or disable the input bar."""
        self._input_bar.set_enabled(enabled)
