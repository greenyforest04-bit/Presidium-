from __future__ import annotations

from typing import Optional, List

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QBrush, QPainterPath, QCursor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QMenu,
    QApplication,
)

from presidium.ui.design_system.tokens import TOKENS, SPACE, TYPE, SHADOW, RADIUS
from presidium.ui.design_system.animations import slide_up, fade_in


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


class ChatBubble(QWidget):
    reply_requested = Signal(str)
    scroll_to_message = Signal(str)

    def __init__(
        self,
        is_own: bool,
        text: str,
        timestamp: str,
        status: str = "sent",
        reply_to: Optional[dict] = None,
        reactions: Optional[List[dict]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._is_own = is_own
        self._text = text
        self._timestamp = timestamp
        self._status = status
        self._reply_to = reply_to
        self._reactions = reactions or []

        self._setup_ui()
        self._apply_styling()
        self._setup_animations()

    def _setup_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, SPACE.XS, 0, SPACE.XS)
        outer.setSpacing(0)

        bubble_container = QWidget()
        bubble_container.setObjectName("chat-bubble")
        bubble_layout = QVBoxLayout(bubble_container)
        bubble_layout.setContentsMargins(SPACE.MD, SPACE.SM, SPACE.MD, SPACE.SM)
        bubble_layout.setSpacing(SPACE.XS)

        if self._reply_to:
            reply_widget = QWidget()
            reply_layout = QHBoxLayout(reply_widget)
            reply_layout.setContentsMargins(SPACE.SM, SPACE.XS, SPACE.SM, SPACE.XS)
            reply_layout.setSpacing(SPACE.XS)

            accent_bar = QLabel()
            accent_bar.setFixedWidth(3)
            accent_bar.setStyleSheet(f"background-color: {TOKENS.ACCENT}; border-radius: 2px;")
            reply_layout.addWidget(accent_bar)

            text_layout = QVBoxLayout()
            text_layout.setSpacing(0)
            text_layout.setContentsMargins(0, 0, 0, 0)

            author_label = QLabel(self._reply_to.get("author", ""))
            author_label.setStyleSheet(f"color: {TOKENS.ACCENT}; font-size: 12px; font-weight: bold;")
            text_layout.addWidget(author_label)

            preview_text = self._reply_to.get("text", "")
            preview_label = QLabel(preview_text)
            preview_label.setStyleSheet(f"color: {TOKENS.TEXT_SECONDARY}; font-size: 12px;")
            preview_label.setMaximumHeight(28)
            preview_label.setWordWrap(True)
            text_layout.addWidget(preview_label)

            reply_layout.addLayout(text_layout, 1)
            reply_layout.addStretch()
            bubble_layout.addWidget(reply_widget)

        self._text_label = QLabel(self._text)
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_label.setMaximumWidth(480)
        bubble_layout.addWidget(self._text_label)

        footer = QHBoxLayout()
        footer.setSpacing(SPACE.SM)

        self._time_label = QLabel(self._timestamp)
        self._time_label.setStyleSheet(
            f"color: {TOKENS.TEXT_MUTED}; font-size: 12px; font-weight: 500;"
        )
        footer.addWidget(self._time_label)

        if self._is_own:
            self._status_label = QLabel()
            self._status_label.setFixedSize(16, 12)
            footer.addWidget(self._status_label)

        footer.addStretch()
        bubble_layout.addLayout(footer)

        if self._reactions:
            reactions_row = QHBoxLayout()
            reactions_row.setSpacing(SPACE.XS)
            for reaction in self._reactions:
                pill = QLabel(f"{reaction.get('emoji', '')} {reaction.get('count', 0)}")
                pill.setStyleSheet(
                    f"background-color: {TOKENS.BG_TERTIARY}; border-radius: {RADIUS.FULL}px; "
                    f"color: {TOKENS.TEXT_PRIMARY}; font-size: 12px; padding: 4px 8px;"
                )
                reactions_row.addWidget(pill)
            bubble_layout.addLayout(reactions_row)

        if self._is_own:
            outer.addStretch()
            outer.addWidget(bubble_container, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(bubble_container, 0, Qt.AlignmentFlag.AlignLeft)
            outer.addStretch()

        self._bubble_container = bubble_container
        self._status_label_widget = self._status_label if self._is_own else None

    def _apply_styling(self) -> None:
        bg = TOKENS.BUBBLE_OWN if self._is_own else TOKENS.BUBBLE_OTHER
        radius = f"{RADIUS.LG}px"
        tail_radius = f"{RADIUS.SM}px"

        border_radius = radius
        if self._is_own:
            border_radius = f"{RADIUS.LG}px {RADIUS.LG}px {RADIUS.LG}px {tail_radius}"
        else:
            border_radius = f"{tail_radius} {RADIUS.LG}px {RADIUS.LG}px {RADIUS.LG}px"

        self._bubble_container.setStyleSheet(
            f"QWidget#chat-bubble {{ background-color: {bg}; border-radius: {border_radius}; }}"
        )

        shadow = QGraphicsDropShadowEffect(self._bubble_container)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 2)
        self._bubble_container.setGraphicsEffect(shadow)

        if self._is_own:
            glow_effect = QGraphicsDropShadowEffect(self._bubble_container)
            glow_effect.setBlurRadius(12)
            glow_effect.setColor(QColor(99, 102, 241, 25))
            glow_effect.setOffset(0, 0)
            self._bubble_container.setGraphicsEffect(glow_effect)

    def _setup_animations(self) -> None:
        self.setWindowOpacity(0.0)
        fade = fade_in(self, duration=250)
        slide = slide_up(self, distance=16, duration=250)
        fade.start()
        slide.start()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self._bubble_container.geometry()
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS.LG, RADIUS.LG)

        if self._is_own:
            path = QPainterPath()
            path.moveTo(rect.right(), rect.top())
            path.lineTo(rect.right() - 12, rect.top())
            path.lineTo(rect.right(), rect.top() + 8)
            path.closeSubpath()
            rounded = QPainterPath()
            rounded.addRoundedRect(rect.adjusted(0, 0, -12, 0), RADIUS.LG, RADIUS.LG)
            path = rounded
        else:
            path = QPainterPath()
            path.moveTo(rect.left(), rect.top())
            path.lineTo(rect.left() + 12, rect.top())
            path.lineTo(rect.left(), rect.top() + 8)
            path.closeSubpath()
            rounded = QPainterPath()
            rounded.addRoundedRect(rect.adjusted(12, 0, 0, 0), RADIUS.LG, RADIUS.LG)
            path = rounded

        painter.fillPath(path, QColor(TOKENS.BUBBLE_OWN if self._is_own else TOKENS.BUBBLE_OTHER))

        self._draw_status(painter, rect)
        super().paintEvent(event)

    def _draw_status(self, painter: QPainter, rect) -> None:
        if not self._is_own or not self._status_label_widget:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color_map = {
            "sent": TOKENS.TEXT_MUTED,
            "delivered": TOKENS.TEXT_MUTED,
            "read": TOKENS.SUCCESS,
            "error": TOKENS.ERROR,
        }
        color = QColor(color_map.get(self._status, TOKENS.TEXT_MUTED))
        pen = QPen(color, 1.5)
        painter.setPen(pen)

        x = rect.right() - 20
        y = rect.bottom() - 14

        if self._status in ("read",):
            painter.drawLine(x, y, x + 6, y + 6)
            painter.drawLine(x + 6, y + 6, x + 12, y - 4)
            painter.drawLine(x + 4, y, x + 10, y + 6)
            painter.drawLine(x + 10, y + 6, x + 16, y - 4)
        elif self._status in ("delivered",):
            painter.drawLine(x, y, x + 6, y + 6)
            painter.drawLine(x + 6, y + 6, x + 12, y - 4)
            painter.drawLine(x + 4, y, x + 10, y + 6)
            painter.drawLine(x + 10, y + 6, x + 16, y - 4)
        else:
            painter.drawLine(x + 4, y + 3, x + 10, y + 3)
            painter.drawLine(x + 7, y, x + 7, y + 6)

    def update_status(self, status: str) -> None:
        self._status = status
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.setStyleSheet(
                f"background-color: {TOKENS.ACCENT}; opacity: 0.15;"
            )
        super().mousePressEvent(event)

    def _show_context_menu(self, pos) -> None:
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

        reply_action = menu.addAction("Reply")
        forward_action = menu.addAction("Forward")
        delete_action = menu.addAction("Delete")

        action = menu.exec(QCursor.pos())

        if action == reply_action:
            self.reply_requested.emit("reply")
        elif action == forward_action:
            pass  # SIMPLIFIED
        elif action == delete_action:
            pass  # SIMPLIFIED
