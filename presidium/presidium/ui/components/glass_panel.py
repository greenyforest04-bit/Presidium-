"""Glassmorphism panel widget for Presidium.

A semi-transparent panel with blur effect, used as a container for
floating UI elements like modal dialogs, toolbars, and overlays.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPainterPath
from PySide6.QtWidgets import QWidget, QGraphicsBlurEffect


class GlassPanel(QWidget):
    """A glassmorphism-style panel with semi-transparent background and blur.

    Usage:
        panel = GlassPanel(parent)
        layout = QVBoxLayout(panel)
        layout.addWidget(some_widget)
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        radius: int = 16,
        opacity: float = 0.85,
        blur_radius: int = 20,
    ) -> None:
        super().__init__(parent)
        self._radius = radius
        self._opacity = opacity
        self._blur_radius = blur_radius

        self.setObjectName("glass-panel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Apply blur effect if supported
        if blur_radius > 0:
            blur = QGraphicsBlurEffect(self)
            blur.setBlurRadius(blur_radius)
            # Note: blur on the panel itself would blur its children too.
            # For true glassmorphism, we'd need to blur the background behind
            # the panel. This is a SIMPLIFIED approach.
            # self.setGraphicsEffect(blur)

    def paintEvent(self, event) -> None:
        """Draw the glass background with rounded corners."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Create rounded rectangle path
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)

        # Fill with semi-transparent color
        painter.setClipPath(path)

        # Background
        bg_color = QColor(26, 26, 36, int(self._opacity * 255))
        painter.fillRect(rect, bg_color)

        # Border
        border_color = QColor(58, 58, 80, int(0.5 * 255))
        painter.setPen(border_color)
        painter.drawPath(path)

        painter.end()

    def set_opacity(self, opacity: float) -> None:
        """Update the panel opacity (0.0 - 1.0)."""
        self._opacity = max(0.0, min(1.0, opacity))
        self.update()

    def set_radius(self, radius: int) -> None:
        """Update the corner radius."""
        self._radius = radius
        self.update()
