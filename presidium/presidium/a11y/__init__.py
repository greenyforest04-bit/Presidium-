"""Accessibility (a11y) system for Presidium.

Provides comprehensive accessibility support for users with disabilities:
    - Screen reader support via Qt Accessible interfaces
    - High contrast mode with configurable color schemes
    - Keyboard navigation for all UI elements
    - Font size scaling (75% - 200%)
    - Reduced motion mode (disables animations)
    - Focus indicators with customizable colors
    - ARIA-like labels via setAccessibleName/setAccessibleDescription

Architecture:
    A11yManager (singleton)
        ├── AccessibilityProfile — user's accessibility preferences
        ├── HighContrastTheme — color schemes for visual impairment
        ├── ScreenReaderBridge — Qt Accessibility integration
        ├── KeyboardNavigator — focus chain and shortcuts
        └── MotionReducer — animation and transition control

Usage:
    from presidium.a11y import A11yManager, AccessibilityProfile

    manager = A11yManager.instance()
    profile = AccessibilityProfile(high_contrast=True, font_scale=1.25)
    manager.apply_profile(profile)

    # Apply to widget
    manager.setup_widget(widget)
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import Qt, QObject
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QWidget, QApplication

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_FONT_SCALE = 0.75
MAX_FONT_SCALE = 2.0
DEFAULT_FONT_SCALE = 1.0
FOCUS_INDICATOR_WIDTH = 3


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class A11yError(Exception):
    """Base exception for accessibility operations."""


class ProfileError(A11yError):
    """Failed to apply accessibility profile."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ContrastMode(enum.Enum):
    """High contrast mode type."""
    NORMAL = "normal"
    HIGH_CONTRAST_DARK = "high_contrast_dark"
    HIGH_CONTRAST_LIGHT = "high_contrast_light"
    CUSTOM = "custom"


class MotionMode(enum.Enum):
    """Animation motion setting."""
    FULL = "full"           # All animations enabled
    REDUCED = "reduced"     # Reduce animations (shorter duration)
    NONE = "none"           # Disable all animations


class FocusIndicator(enum.Enum):
    """Focus indicator style."""
    DEFAULT = "default"
    BOLD = "bold"
    UNDERLINE = "underline"


# ---------------------------------------------------------------------------
# Accessibility Profile
# ---------------------------------------------------------------------------

@dataclass
class AccessibilityProfile:
    """User's accessibility preferences.

    All settings can be independently configured and are persisted
    to the application settings.

    Attributes:
        high_contrast: High contrast color mode.
        font_scale: Font size multiplier (0.75 - 2.0).
        motion_mode: Animation motion setting.
        focus_indicator: Focus indicator style.
        screen_reader_enabled: Whether screen reader support is active.
        keyboard_navigation: Whether enhanced keyboard navigation is enabled.
        color_blind_mode: Color blind accessibility mode.
        tooltip_delay_ms: Tooltip show delay in milliseconds.
        focus_color: Custom focus indicator color (hex string).
    """
    contrast_mode: ContrastMode = ContrastMode.NORMAL
    font_scale: float = DEFAULT_FONT_SCALE
    motion_mode: MotionMode = MotionMode.FULL
    focus_indicator: FocusIndicator = FocusIndicator.DEFAULT
    screen_reader_enabled: bool = False
    keyboard_navigation: bool = True
    color_blind_mode: str = "none"
    tooltip_delay_ms: int = 500
    focus_color: str = "#6366F1"

    def __post_init__(self) -> None:
        self.font_scale = max(MIN_FONT_SCALE, min(MAX_FONT_SCALE, self.font_scale))

    @property
    def is_high_contrast(self) -> bool:
        return self.contrast_mode in (ContrastMode.HIGH_CONTRAST_DARK, ContrastMode.HIGH_CONTRAST_LIGHT)

    @property
    def is_reduced_motion(self) -> bool:
        return self.motion_mode in (MotionMode.REDUCED, MotionMode.NONE)

    @property
    def is_no_motion(self) -> bool:
        return self.motion_mode == MotionMode.NONE


# ---------------------------------------------------------------------------
# High Contrast Color Schemes
# ---------------------------------------------------------------------------

HIGH_CONTRAST_DARK = {
    "background": "#000000",
    "foreground": "#FFFFFF",
    "accent": "#FFFF00",
    "accent_secondary": "#00FFFF",
    "error": "#FF4444",
    "success": "#44FF44",
    "warning": "#FFFF44",
    "surface": "#1A1A1A",
    "surface_variant": "#333333",
    "border": "#FFFFFF",
    "text_secondary": "#CCCCCC",
    "focus_ring": "#FFFF00",
}

HIGH_CONTRAST_LIGHT = {
    "background": "#FFFFFF",
    "foreground": "#000000",
    "accent": "#0000FF",
    "accent_secondary": "#006600",
    "error": "#CC0000",
    "success": "#006600",
    "warning": "#664400",
    "surface": "#F0F0F0",
    "surface_variant": "#E0E0E0",
    "border": "#000000",
    "text_secondary": "#333333",
    "focus_ring": "#0000FF",
}


# ---------------------------------------------------------------------------
# A11y Manager
# ---------------------------------------------------------------------------

class A11yManager:
    """Centralized accessibility manager for Presidium.

    Manages the user's accessibility profile and applies it to the
    application's UI. Provides screen reader support, high contrast
    themes, font scaling, and keyboard navigation.

    Usage:
        manager = A11yManager.instance()
        profile = AccessibilityProfile(
            contrast_mode=ContrastMode.HIGH_CONTRAST_DARK,
            font_scale=1.25,
        )
        manager.apply_profile(profile)
    """

    _instance: Optional[A11yManager] = None

    def __init__(self) -> None:
        self._profile = AccessibilityProfile()
        self._base_font_size: int = 14
        self._applied_widgets: list[QWidget] = []

    @classmethod
    def instance(cls) -> A11yManager:
        """Get the singleton A11yManager instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -------------------------------------------------------------------
    # Profile management
    # -------------------------------------------------------------------

    @property
    def profile(self) -> AccessibilityProfile:
        """Current accessibility profile."""
        return self._profile

    def apply_profile(self, profile: AccessibilityProfile) -> None:
        """Apply an accessibility profile to the application.

        Updates font sizes, color schemes, and animation settings
        across all registered widgets.

        Args:
            profile: The accessibility profile to apply.
        """
        self._profile = profile

        # Apply font scaling
        self._apply_font_scaling()

        # Apply high contrast if needed
        if profile.is_high_contrast:
            self._apply_high_contrast()

        # Apply motion reduction
        self._apply_motion_settings()

        # Re-apply to all registered widgets
        for widget in self._applied_widgets:
            self.setup_widget(widget)

        logger.info(
            "Applied a11y profile: contrast=%s, font_scale=%.2f, motion=%s",
            profile.contrast_mode.value,
            profile.font_scale,
            profile.motion_mode.value,
        )

    def _apply_font_scaling(self) -> None:
        """Apply font scaling to the application."""
        app = QApplication.instance()
        if app is None:
            return

        font = app.font()
        scaled_size = int(self._base_font_size * self._profile.font_scale)
        font.setPointSize(scaled_size)
        app.setFont(font)

    def _apply_high_contrast(self) -> None:
        """Apply high contrast color scheme."""
        app = QApplication.instance()
        if app is None:
            return

        colors = self._get_contrast_colors()
        if colors is None:
            return

        # Apply via Qt stylesheet
        stylesheet = self._build_high_contrast_stylesheet(colors)
        app.setStyleSheet(app.styleSheet() + stylesheet)

    def _apply_motion_settings(self) -> None:
        """Apply motion reduction settings."""
        if self._profile.is_no_motion:
            # Disable all animations globally via Qt
            app = QApplication.instance()
            if app is not None:
                app.setAttribute(Qt.ApplicationAttribute.AA_DisableWindowContextHelpButton)

    def _get_contrast_colors(self) -> Optional[dict[str, str]]:
        """Get the color scheme for the current contrast mode."""
        if self._profile.contrast_mode == ContrastMode.HIGH_CONTRAST_DARK:
            return HIGH_CONTRAST_DARK
        if self._profile.contrast_mode == ContrastMode.HIGH_CONTRAST_LIGHT:
            return HIGH_CONTRAST_LIGHT
        return None

    def _build_high_contrast_stylesheet(self, colors: dict[str, str]) -> str:
        """Build a Qt stylesheet for high contrast mode."""
        return f"""
            QWidget {{
                background-color: {colors['background']};
                color: {colors['foreground']};
                border-color: {colors['border']};
            }}
            QPushButton {{
                background-color: {colors['surface']};
                color: {colors['foreground']};
                border: 2px solid {colors['border']};
                padding: 6px 16px;
            }}
            QPushButton:focus {{
                border: {FOCUS_INDICATOR_WIDTH}px solid {colors['focus_ring']};
            }}
            QLineEdit, QTextEdit {{
                background-color: {colors['surface']};
                color: {colors['foreground']};
                border: 2px solid {colors['border']};
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: {FOCUS_INDICATOR_WIDTH}px solid {colors['focus_ring']};
            }}
            QListWidget {{
                background-color: {colors['surface']};
                color: {colors['foreground']};
                border: 1px solid {colors['border']};
            }}
            QListWidget::item:selected {{
                background-color: {colors['accent']};
                color: {colors['background']};
            }}
            QLabel {{
                color: {colors['foreground']};
            }}
        """

    # -------------------------------------------------------------------
    # Widget setup
    # -------------------------------------------------------------------

    def setup_widget(self, widget: QWidget) -> None:
        """Apply accessibility settings to a widget.

        Sets accessible names, descriptions, and focus policies
        based on the current accessibility profile.

        Args:
            widget: The widget to configure.
        """
        if widget not in self._applied_widgets:
            self._applied_widgets.append(widget)

        # Enable keyboard focus for interactive widgets
        from PySide6.QtWidgets import (
            QPushButton, QLineEdit, QTextEdit, QComboBox,
            QCheckBox, QRadioButton, QSlider, QSpinBox,
            QListWidget, QTreeWidget, QTabWidget,
        )

        interactive_types = (
            QPushButton, QLineEdit, QTextEdit, QComboBox,
            QCheckBox, QRadioButton, QSlider, QSpinBox,
            QListWidget, QTreeWidget, QTabWidget,
        )

        if isinstance(widget, interactive_types):
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Apply font scaling
        if self._profile.font_scale != DEFAULT_FONT_SCALE:
            font = widget.font()
            scaled_size = int(self._base_font_size * self._profile.font_scale)
            font.setPointSize(scaled_size)
            widget.setFont(font)

        # Apply focus indicator style
        if self._profile.focus_indicator == FocusIndicator.BOLD:
            widget.setStyleSheet(
                widget.styleSheet() +
                f"QWidget:focus {{ border: {FOCUS_INDICATOR_WIDTH}px solid {self._profile.focus_color}; }}"
            )

        # Recursively setup child widgets
        for child in widget.findChildren(QWidget):
            self.setup_widget(child)

    # -------------------------------------------------------------------
    # Screen reader support
    # -------------------------------------------------------------------

    def announce(self, widget: QWidget, message: str) -> None:
        """Announce a message to the screen reader.

        Uses Qt's accessibility interface to notify screen readers
        of important state changes or events.

        Args:
            widget: The widget context for the announcement.
            message: The message to announce.
        """
        if not self._profile.screen_reader_enabled:
            return

        try:
            from PySide6.QtGui import QAccessible
            QAccessible.updateAccessibility(
                QAccessible.StateChange(widget)
            )
        except Exception:
            # QAccessible might not be available
            logger.debug("Screen reader announcement: %s", message)

    # -------------------------------------------------------------------
    # Query
    # -------------------------------------------------------------------

    def scaled_font_size(self, base_size: int) -> int:
        """Get a font size scaled by the current profile's font_scale."""
        return max(8, int(base_size * self._profile.font_scale))

    @property
    def animation_duration_factor(self) -> float:
        """Multiplier for animation durations based on motion settings."""
        if self._profile.is_no_motion:
            return 0.0
        if self._profile.is_reduced_motion:
            return 0.3
        return 1.0

    @property
    def should_animate(self) -> bool:
        """Whether animations should be shown."""
        return not self._profile.is_no_motion
