from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont, QPixmap, QCursor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QLineEdit,
    QMenu,
)

from presidium.core.messaging.chat import ChatSession
from presidium.ui.components.identicon import generate_identicon_pixmap
from presidium.ui.design_system.tokens import TOKENS, SPACE, TYPE, SHADOW, RADIUS
from presidium.ui.design_system.animations import fade_in


class ChatListItem(QWidget):
    clicked = Signal(str)
    context_requested = Signal(str, QCursor)

    def __init__(
        self,
        session: ChatSession,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._hovered = False
        self._selected = False
        self.setObjectName("chat-list-item")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(64)
        self.setMaximumHeight(72)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE.LG, SPACE.SM, SPACE.LG, SPACE.SM)
        layout.setSpacing(SPACE.MD)

        avatar_label = QLabel()
        avatar_label.setFixedSize(40, 40)
        avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            if self._session.peer_public_key:
                pixmap = generate_identicon_pixmap(self._session.peer_public_key, 40)
                avatar_label.setPixmap(pixmap)
            else:
                avatar_label.setText("👤")
                avatar_label.setStyleSheet(f"color: {TOKENS.TEXT_MUTED}; font-size: 18px;")
        except Exception:
            avatar_label.setText("👤")
        layout.addWidget(avatar_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        top_row = QHBoxLayout()
        self._name_label = QLabel(self._session.display_name)
        self._name_label.setStyleSheet(
            f"color: {TOKENS.TEXT_PRIMARY}; font-size: 14px; font-weight: 500;"
        )
        top_row.addWidget(self._name_label, 1)

        self._time_label = QLabel()
        self._time_label.setStyleSheet(
            f"color: {TOKENS.TEXT_MUTED}; font-size: 12px; font-weight: 500;"
        )
        top_row.addWidget(self._time_label)
        text_layout.addLayout(top_row)

        self._preview_label = QLabel()
        self._preview_label.setStyleSheet(
            f"color: {TOKENS.TEXT_SECONDARY}; font-size: 12px;"
        )
        self._preview_label.setMaximumWidth(260)
        text_layout.addWidget(self._preview_label)

        self._badge_label = QLabel()
        self._badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge_label.setFixedSize(20, 20)
        self._badge_label.setStyleSheet(
            f"background-color: {TOKENS.ACCENT}; color: white; border-radius: 10px; font-size: 11px; font-weight: bold;"
        )

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._preview_label, 1)
        bottom_row.addWidget(self._badge_label)
        text_layout.addLayout(bottom_row)

        layout.addLayout(text_layout, 1)
        self._update_display()

    def _update_display(self) -> None:
        if self._session.last_message_timestamp > 0:
            self._time_label.setText(format_timestamp(self._session.last_message_timestamp))

        self._preview_label.setText(self._session.last_message_preview or "No messages yet")

        if self._session.unread_count > 0:
            self._badge_label.setText(str(self._session.unread_count))
            self._badge_label.show()
        else:
            self._badge_label.hide()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = TOKENS.BG_ELEVATED if self._selected else TOKENS.BG_PRIMARY
        if self._hovered and not self._selected:
            bg = "rgba(255, 255, 255, 0.03)"

        painter.fillRect(self.rect(), QColor(bg))

        if self._selected:
            painter.fillRect(0, 0, 3, self.height(), QColor(TOKENS.ACCENT))

        super().paintEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(self._session.peer_user_id, QCursor.pos())
        else:
            self.clicked.emit(self._session.peer_user_id)
        super().mousePressEvent(event)

    def update_session(self, session: ChatSession) -> None:
        self._session = session
        self._name_label.setText(session.display_name)
        self._update_display()
        self.update()


class SearchBar(QWidget):
    search_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE.LG, SPACE.SM, SPACE.LG, SPACE.SM)
        layout.setSpacing(SPACE.SM)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Search")
        self._input.setStyleSheet(
            f"background-color: {TOKENS.BG_TERTIARY}; color: {TOKENS.TEXT_MUTED}; "
            f"border-radius: {RADIUS.MD}px; padding: 8px 12px; border: none;"
        )
        self._input.textChanged.connect(self.search_changed.emit)
        layout.addWidget(self._input, 1)


class ChatListWidget(QScrollArea):
    chat_selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(f"background-color: {TOKENS.BG_PRIMARY};")

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._search_bar = SearchBar()
        self._layout.addWidget(self._search_bar)

        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(0)
        self._layout.addLayout(self._list_layout)
        self._layout.addStretch()

        self.setWidget(self._container)

        self._items: dict[str, ChatListItem] = {}
        self._typing_users: dict[str, QWidget] = {}

    def set_sessions(self, sessions: list[ChatSession]) -> None:
        for item in self._items.values():
            self._list_layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()

        for session in sessions:
            self.add_session(session)

    def add_session(self, session: ChatSession) -> None:
        existing = self._items.get(session.peer_user_id)
        if existing:
            existing.update_session(session)
            return

        item = ChatListItem(session, self._container)
        item.clicked.connect(self.chat_selected.emit)
        item.context_requested.connect(self._show_context_menu)
        self._list_layout.addWidget(item)
        self._items[session.peer_user_id] = item

    def remove_session(self, peer_user_id: str) -> None:
        item = self._items.pop(peer_user_id, None)
        if item:
            self._list_layout.removeWidget(item)
            item.deleteLater()

    def highlight_session(self, peer_user_id: str) -> None:
        item = self._items.get(peer_user_id)
        if item:
            self._list_layout.removeWidget(item)
            self._list_layout.insertWidget(0, item)

    def _show_context_menu(self, peer_user_id: str, pos: QCursor) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {TOKENS.GLASS_BG};
                color: {TOKENS.TEXT_PRIMARY};
                border: 1px solid {TOKENS.GLASS_BORDER};
                border-radius: {RADIUS.MD}px;
                padding: 4px;
            }}
            QMenu::item:selected {{
                background-color: rgba(255, 255, 255, 0.05);
                border-radius: {RADIUS.SM}px;
            }}
        """)

        for label in ("Mark Read", "Mute", "Archive", "Delete"):
            menu.addAction(label)

        target_pos = self.mapFromGlobal(pos)
        menu.exec(self.mapToGlobal(target_pos))

    def sizeHint(self) -> QSize:
        return QSize(360, 600)


def format_timestamp(ts: float) -> str:
    import time
    from datetime import datetime
    now = time.time()
    diff = now - ts
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    else:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M")
