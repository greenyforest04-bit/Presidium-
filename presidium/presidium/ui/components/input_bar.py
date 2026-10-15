from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QGraphicsDropShadowEffect,
    QFrame,
)

from presidium.ui.design_system.tokens import TOKENS, SPACE, RADIUS
from presidium.ui.design_system.animations import spring_scale, fade_in


class ReplyPreviewPanel(QWidget):
    cancelled = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._reply_to: Optional[dict] = None
        self.setObjectName("reply-preview")
        self.setFixedHeight(0)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE.LG, SPACE.SM, SPACE.LG, SPACE.SM)
        layout.setSpacing(SPACE.SM)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        accent_bar = QFrame()
        accent_bar.setFixedWidth(3)
        accent_bar.setStyleSheet(f"background-color: {TOKENS.ACCENT}; border-radius: 2px;")
        layout.addWidget(accent_bar)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)
        info_layout.setContentsMargins(0, 0, 0, 0)

        self._author_label = QLabel()
        self._author_label.setStyleSheet(f"color: {TOKENS.ACCENT}; font-size: 12px; font-weight: 600;")
        info_layout.addWidget(self._author_label)

        self._text_label = QLabel()
        self._text_label.setStyleSheet(f"color: {TOKENS.TEXT_SECONDARY}; font-size: 12px;")
        self._text_label.setMaximumHeight(18)
        self._text_label.setWordWrap(True)
        info_layout.addWidget(self._text_label)

        layout.addLayout(info_layout, 1)

        cancel_btn = QPushButton("\u2715")
        cancel_btn.setFixedSize(24, 24)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TOKENS.TEXT_MUTED}; "
            f"border: none; border-radius: 12px; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {TOKENS.TEXT_PRIMARY}; "
            f"background-color: rgba(255,255,255,0.05); }}"
        )
        cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(cancel_btn)

    def show_reply(self, author: str, text: str) -> None:
        self._reply_to = {"author": author, "text": text}
        self._author_label.setText(author)
        self._text_label.setText(text[:80] + ("..." if len(text) > 80 else ""))
        self._animate_height(48)

    def clear_reply(self) -> None:
        self._reply_to = None
        self._author_label.clear()
        self._text_label.clear()
        self._animate_height(0)
        self.cancelled.emit()

    @property
    def reply_target(self) -> Optional[dict]:
        return self._reply_to

    def _animate_height(self, target: int) -> None:
        anim = QPropertyAnimation(self, b"fixedHeight")
        anim.setDuration(200)
        anim.setStartValue(self.height())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()

    def _on_cancel(self) -> None:
        self.clear_reply()


class PillInputBar(QWidget):
    send_message = Signal(str)
    attach_file = Signal()
    voice_call = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("pill-input")
        self.setFixedHeight(64)
        self._has_text = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._reply_panel = ReplyPreviewPanel(self)
        outer.addWidget(self._reply_panel)

        pill = QWidget()
        pill.setObjectName("input-pill")
        pill.setFixedHeight(48)
        pill.setStyleSheet(
            f"QWidget#input-pill {{ background-color: {TOKENS.BG_ELEVATED}; "
            f"border-radius: 24px; }}"
        )

        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(SPACE.XS, SPACE.XS, SPACE.XS, SPACE.XS)
        pill_layout.setSpacing(SPACE.XS)

        attach_btn = QPushButton()
        attach_btn.setFixedSize(36, 36)
        attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        attach_btn.setToolTip("Attach file")
        attach_btn.clicked.connect(self.attach_file.emit)
        attach_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {TOKENS.TEXT_MUTED}; font-size: 18px; }}"
            f"QPushButton:hover {{ color: {TOKENS.TEXT_PRIMARY}; }}"
        )
        self._draw_svg_icon(attach_btn, "\U0001f4ce")
        pill_layout.addWidget(attach_btn)

        emoji_btn = QPushButton()
        emoji_btn.setFixedSize(36, 36)
        emoji_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        emoji_btn.setToolTip("Emoji")
        emoji_btn.setStyleSheet(attach_btn.styleSheet())
        self._draw_svg_icon(emoji_btn, "\U0001f642")
        pill_layout.addWidget(emoji_btn)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Message...")
        self._input.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; "
            f"color: {TOKENS.TEXT_PRIMARY}; font-size: 14px; "
            f"font-weight: 400; padding: 0 {SPACE.SM}px; }}"
            f"QLineEdit::placeholder {{ color: {TOKENS.TEXT_MUTED}; }}"
        )
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._on_send)
        pill_layout.addWidget(self._input, 1)

        self._voice_btn = QPushButton()
        self._voice_btn.setFixedSize(36, 36)
        self._voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._voice_btn.setToolTip("Voice message (hold)")
        self._voice_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {TOKENS.TEXT_MUTED}; font-size: 18px; }}"
            f"QPushButton:hover {{ color: {TOKENS.TEXT_PRIMARY}; }}"
        )
        self._draw_svg_icon(self._voice_btn, "\U0001f399")
        self._voice_btn.pressed.connect(self.voice_call.emit)
        pill_layout.addWidget(self._voice_btn)

        self._send_btn = QPushButton()
        self._send_btn.setFixedSize(0, 36)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("Send")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background-color: {TOKENS.ACCENT}; "
            f"border: none; border-radius: 18px; "
            f"color: white; font-size: 16px; }}"
            f"QPushButton:hover {{ background-color: {TOKENS.ACCENT_HOVER}; }}"
        )
        self._draw_svg_icon(self._send_btn, "\u27a4")
        self._send_btn.hide()
        pill_layout.addWidget(self._send_btn)

        pill_layout.addSpacing(SPACE.XS)

        pill_shadow = QGraphicsDropShadowEffect(pill)
        pill_shadow.setBlurRadius(12)
        pill_shadow.setColor(QColor(0, 0, 0, 60))
        pill_shadow.setOffset(0, 2)
        pill.setGraphicsEffect(pill_shadow)

        outer.addWidget(pill, alignment=Qt.AlignmentFlag.AlignBottom)
        outer.addSpacing(SPACE.XS)

    def _draw_svg_icon(self, btn: QPushButton, icon_text: str) -> None:
        btn.setText(icon_text)

    def _on_text_changed(self, text: str) -> None:
        has_text = bool(text.strip())
        if has_text == self._has_text:
            return
        self._has_text = has_text
        if has_text:
            self._voice_btn.hide()
            self._send_btn.setFixedSize(36, 36)
            self._send_btn.show()
            spring_scale(self._send_btn, from_scale=0.0, to_scale=1.0, duration=200).start()
        else:
            self._send_btn.hide()
            self._send_btn.setFixedSize(0, 36)
            self._voice_btn.show()

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if text:
            self.send_message.emit(text)
            self._input.clear()
            self._reply_panel.clear_reply()

    def set_reply_to(self, author: str, text: str) -> None:
        self._reply_panel.show_reply(author, text)
        self._input.setFocus()

    def clear_reply(self) -> None:
        self._reply_panel.clear_reply()

    @property
    def reply_target(self) -> Optional[dict]:
        return self._reply_panel.reply_target

    def set_enabled(self, enabled: bool) -> None:
        self._input.setEnabled(enabled)
