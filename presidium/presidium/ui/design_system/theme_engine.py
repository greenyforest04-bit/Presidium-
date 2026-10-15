from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from presidium.ui.design_system.tokens import TOKENS, LightColors, Colors
from presidium.ui.styles.dark_theme import get_dark_stylesheet
from presidium.ui.styles.light_theme import get_light_stylesheet

logger = logging.getLogger(__name__)


class ThemeMode(Enum):
    DARK = "dark"
    LIGHT = "light"
    OLED = "oled"
    SYSTEM = "system"


class ThemeEngine(QObject):
    theme_changed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._mode: ThemeMode = ThemeMode.DARK
        self._accent_color: str = "#6366F1"
        self._app: Optional[QApplication] = None

    def initialize(self, app: QApplication) -> None:
        self._app = app
        self._detect_system_theme()
        self.apply_theme(self._mode)
        logger.info("ThemeEngine initialized with mode: %s", self._mode.value)

    def _detect_system_theme(self) -> None:
        if self._app:
            try:
                scheme = self._app.styleHints().colorScheme()
                if scheme == self._app.styleHints().ColorScheme.Dark:
                    self._mode = ThemeMode.DARK
                else:
                    self._mode = ThemeMode.LIGHT
            except Exception:
                self._mode = ThemeMode.DARK

    def apply_theme(self, mode: ThemeMode) -> None:
        self._mode = mode
        self._update_tokens()
        stylesheet = self._get_stylesheet()
        if self._app:
            self._app.setStyleSheet(stylesheet)
        self.theme_changed.emit(mode.value)
        logger.info("Theme applied: %s", mode.value)

    def _update_tokens(self) -> None:
        if self._mode == ThemeMode.LIGHT:
            colors = LightColors()
            TOKENS.__dict__.update(
                {k: v for k, v in LightColors.__dataclass_fields__.items()
                 if hasattr(colors, k)}
            )
        else:
            colors = Colors()
            TOKENS.__dict__.update(
                {k: v for k, v in Colors.__dataclass_fields__.items()
                 if hasattr(colors, k)}
            )

        if self._mode == ThemeMode.OLED:
            TOKENS.BG_PRIMARY = "#000000"
            TOKENS.BG_SECONDARY = "#0A0A0A"

        if self._accent_color != "#6366F1":
            self._apply_accent_color(self._accent_color)

    def _get_stylesheet(self) -> str:
        if self._mode == ThemeMode.LIGHT:
            return get_light_stylesheet()
        return get_dark_stylesheet()

    def _apply_accent_color(self, color: str) -> None:
        qcolor = QColor(color)
        TOKENS.ACCENT = color
        TOKENS.ACCENT_HOVER = qcolor.lighter(110).name()
        TOKENS.ACCENT_PRESSED = qcolor.darker(110).name()
        TOKENS.ACCENT_GLOW = f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, 0.15)"

    def set_accent_color(self, color: str) -> None:
        self._accent_color = color
        self.apply_theme(self._mode)

    def extract_accent_from_wallpaper(self, image_path: str) -> str:
        try:
            from PIL import Image
            img = Image.open(image_path)
            img = img.resize((1, 1))
            pixel = img.getpixel((0, 0))
            return f"#{pixel[0]:02x}{pixel[1]:02x}{pixel[2]:02x}"
        except Exception as exc:
            logger.warning("Failed to extract accent from wallpaper: %s", exc)
            return "#6366F1"

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @mode.setter
    def mode(self, value: ThemeMode) -> None:
        self.apply_theme(value)

    def toggle(self) -> None:
        if self._mode == ThemeMode.DARK:
            self.apply_theme(ThemeMode.LIGHT)
        elif self._mode == ThemeMode.LIGHT:
            self.apply_theme(ThemeMode.OLED if hasattr(self, '_oled_enabled') else ThemeMode.DARK)
        else:
            self.apply_theme(ThemeMode.DARK)


theme_engine = ThemeEngine()
